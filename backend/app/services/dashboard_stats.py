"""概览图表快照与请求日志日聚合。

请求只读 `dashboard_stats_snapshots`。后台任务不再每轮重扫整张大表：先把请求日志按
上海自然日增量聚合成 `request_log_daily_stats`，再从日行拼出 7/14/30/90 天快照，
并为仪表盘累计值与 Key 用量提供小结果集。首轮回填最近 10 年，之后只重算有新增/续写
日志的日期。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.clock import SHANGHAI_TZ, shanghai_day_start_utc, to_shanghai, utcnow
from app.models import DashboardStatsMeta, RequestLog, RequestLogDailyStats
from app.models import DashboardStatsSnapshot as StatsRow
from app.schemas import (
    DashboardStatsAccountOut,
    DashboardStatsDayOut,
    DashboardStatsHourOut,
    DashboardStatsModelOut,
    DashboardStatsOut,
    DashboardStatsSummaryOut,
)
from app.services.stats_scan import STATS_SCAN_LOCK, yield_to_others

STATS_MAX_DAYS = 90
# 前端区间档位。任务每次把这几档从日聚合拼一遍，切区间不用等。
SNAPSHOT_DAYS = (7, 14, 30, 90)
# 日聚合覆盖最近 10 年（用于仪表盘累计值）；上限防止异常时间戳导致无限回溯，
# 超过 10 年的历史不计入累计值。
ROLLUP_MAX_DAYS = 3650
# 结构版本：字段变化时自增，旧日聚合会被判定为待重算。
ROLLUP_SCHEMA = 2

# created_at 存的是 naive UTC，图表按上海自然日/小时分桶，统一在 SQL 里 +8 小时
_DAY_EXPR = func.strftime("%Y-%m-%d", RequestLog.created_at, "+8 hours")
_HOUR_EXPR = func.strftime("%H", RequestLog.created_at, "+8 hours")
_TOKEN_EXPR = func.coalesce(
    func.sum(
        func.coalesce(
            RequestLog.total_tokens,
            func.coalesce(RequestLog.prompt_tokens, 0) + func.coalesce(RequestLog.completion_tokens, 0),
        )
    ),
    0,
)
_FAIL_EXPR = func.coalesce(func.sum(case((RequestLog.status == "error", 1), else_=0)), 0)
_CALLS_EXPR = func.coalesce(func.sum(func.coalesce(RequestLog.turn_count, 1)), 0)
_LATENCY_EXPR = func.coalesce(
    RequestLog.last_latency_ms,
    case(
        (func.coalesce(RequestLog.turn_count, 1) <= 1, RequestLog.latency_ms),
        else_=None,
    ),
)
_LATENCY_OK_EXPR = case((RequestLog.status != "error", _LATENCY_EXPR), else_=None)
_LATENCY_SUM_EXPR = func.coalesce(func.sum(_LATENCY_OK_EXPR), 0)
_LATENCY_COUNT_EXPR = func.count(_LATENCY_OK_EXPR)
_MODEL_EXPR = func.coalesce(func.nullif(RequestLog.model, ""), "未知模型")
_ACCOUNT_EXPR = func.coalesce(func.nullif(RequestLog.account_name, ""), "未知上游")
_KEY_EXPR = RequestLog.api_key_id


def snapshot_days() -> tuple[int, ...]:
    return SNAPSHOT_DAYS


def empty_stats(days: int) -> DashboardStatsOut:
    today = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")
    start = (datetime.now(SHANGHAI_TZ) - timedelta(days=max(days, 1) - 1)).strftime("%Y-%m-%d")
    return DashboardStatsOut(days=days, start_date=start, end_date=today)


def load_snapshot(db: Session, days: int) -> DashboardStatsOut | None:
    row = db.get(StatsRow, days)
    if row is None or not row.payload_json:
        return None
    try:
        payload = json.loads(row.payload_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["computed_at"] = row.computed_at
    try:
        return DashboardStatsOut.model_validate(payload)
    except Exception:
        return None


def _save_snapshot(db: Session, days: int, stats: DashboardStatsOut) -> DashboardStatsOut:
    computed_at = utcnow()
    stats.computed_at = computed_at
    payload = stats.model_dump(mode="json")
    payload.pop("computed_at", None)
    row = db.get(StatsRow, days)
    encoded = json.dumps(payload, ensure_ascii=False)
    if row is None:
        db.add(
            StatsRow(
                days=days,
                payload_json=encoded,
                computed_at=computed_at,
                error_message=None,
            )
        )
    else:
        row.payload_json = encoded
        row.computed_at = computed_at
        row.error_message = None
    db.commit()
    return stats


def refresh_snapshot(db: Session, days: int) -> DashboardStatsOut:
    """手动刷新：先补齐日聚合，再从聚合拼快照，避免当场全表扫描。"""
    bounded = max(1, min(int(days), STATS_MAX_DAYS))
    ensure_daily_rollups(db)
    return refresh_snapshot_from_rollups(db, bounded)


def record_snapshot_error(db: Session, days: int, message: str) -> None:
    row = db.get(StatsRow, days)
    if row is None:
        db.add(
            StatsRow(
                days=days,
                payload_json="{}",
                computed_at=utcnow() - timedelta(days=365),
                error_message=message[:500],
            )
        )
    else:
        row.error_message = message[:500]
    db.commit()


def _day_start_utc(day: date) -> datetime:
    local = datetime.combine(day, time.min, tzinfo=SHANGHAI_TZ)
    return local.astimezone(UTC).replace(tzinfo=None)


def _load_json_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_histogram(raw: str | None) -> Counter[int]:
    histogram: Counter[int] = Counter()
    for key, value in _load_json_dict(raw).items():
        try:
            histogram[int(key)] += int(value)
        except (TypeError, ValueError):
            continue
    return histogram


def _p95_from_histogram(histogram: Counter[int] | dict[int, int]) -> int | None:
    if not histogram:
        return None
    total = sum(int(value) for value in histogram.values())
    if total <= 0:
        return None
    offset = min(total - 1, max(0, int(round((total - 1) * 0.95))))
    seen = 0
    for value in sorted(histogram):
        seen += int(histogram[value])
        if seen > offset:
            return int(value)
    return int(max(histogram))


def _compute_day_rollup(db: Session, day: date) -> dict[str, Any]:
    """扫描一个上海自然日，返回可直接写入日聚合行的数据。"""
    start = _day_start_utc(day)
    end = _day_start_utc(day + timedelta(days=1))
    window = (RequestLog.created_at >= start, RequestLog.created_at < end)

    summary = db.execute(
        select(
            func.count(),
            _CALLS_EXPR,
            _FAIL_EXPR,
            _TOKEN_EXPR,
            func.coalesce(func.sum(func.coalesce(RequestLog.prompt_tokens, 0)), 0),
            func.coalesce(func.sum(func.coalesce(RequestLog.completion_tokens, 0)), 0),
            _LATENCY_SUM_EXPR,
            _LATENCY_COUNT_EXPR,
        ).where(*window)
    ).one()
    log_count, requests, failures, tokens, prompt_tokens, completion_tokens, latency_sum, latency_count = summary

    models = {
        str(model): {"requests": int(count or 0), "tokens": int(tokens_value or 0)}
        for model, count, tokens_value in db.execute(
            select(_MODEL_EXPR, _CALLS_EXPR, _TOKEN_EXPR).where(*window).group_by(_MODEL_EXPR)
        ).all()
    }
    accounts = {
        str(account): {"requests": int(count or 0), "tokens": int(tokens_value or 0)}
        for account, count, tokens_value in db.execute(
            select(_ACCOUNT_EXPR, _CALLS_EXPR, _TOKEN_EXPR).where(*window).group_by(_ACCOUNT_EXPR)
        ).all()
    }
    keys = {
        (str(key_id) if key_id is not None else "unknown"): {
            "requests": int(count or 0),
            "tokens": int(tokens_value or 0),
        }
        for key_id, count, tokens_value in db.execute(
            select(_KEY_EXPR, _CALLS_EXPR, _TOKEN_EXPR).where(*window).group_by(_KEY_EXPR)
        ).all()
    }
    hourly = {
        str(int(hour)): int(count or 0)
        for hour, count in db.execute(
            select(_HOUR_EXPR, _CALLS_EXPR).where(*window).group_by(_HOUR_EXPR)
        ).all()
        if hour is not None
    }
    latency_hist = {
        str(int(latency)): int(count or 0)
        for latency, count in db.execute(
            select(_LATENCY_EXPR, func.count())
            .where(*window, RequestLog.status != "error", _LATENCY_EXPR.is_not(None))
            .group_by(_LATENCY_EXPR)
        ).all()
        if latency is not None
    }
    return {
        "log_count": int(log_count or 0),
        "requests": int(requests or 0),
        "failures": int(failures or 0),
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "tokens": int(tokens or 0),
        "latency_sum": int(latency_sum or 0),
        "latency_count": int(latency_count or 0),
        "models": models,
        "accounts": accounts,
        "keys": keys,
        "hourly": hourly,
        "latency_hist": latency_hist,
    }


def _write_rollup(db: Session, day: date, data: dict[str, Any]) -> None:
    key = day.isoformat()
    row = db.get(RequestLogDailyStats, key)
    if row is None:
        row = RequestLogDailyStats(day=key)
        db.add(row)
    row.log_count = int(data["log_count"])
    row.requests = int(data["requests"])
    row.failures = int(data["failures"])
    row.prompt_tokens = int(data["prompt_tokens"])
    row.completion_tokens = int(data["completion_tokens"])
    row.tokens = int(data["tokens"])
    row.latency_sum = int(data["latency_sum"])
    row.latency_count = int(data["latency_count"])
    row.models_json = json.dumps(data["models"], ensure_ascii=False)
    row.accounts_json = json.dumps(data["accounts"], ensure_ascii=False)
    row.keys_json = json.dumps(data["keys"], ensure_ascii=False)
    row.hourly_json = json.dumps(data["hourly"], ensure_ascii=False)
    row.latency_hist_json = json.dumps(data["latency_hist"], ensure_ascii=False)
    row.computed_at = utcnow()
    db.commit()


def _rollup_range(db: Session) -> tuple[date, date] | None:
    """返回日聚合应覆盖的 [最早日志日, 今天]；没有日志时返回 None。"""
    earliest = db.scalar(select(func.min(RequestLog.created_at)))
    db.rollback()
    if earliest is None:
        return None
    today = datetime.now(SHANGHAI_TZ).date()
    oldest = max(today - timedelta(days=ROLLUP_MAX_DAYS - 1), to_shanghai(earliest).date())
    return oldest, today


def ensure_daily_rollups(db: Session) -> None:
    """增量补齐请求日志的日聚合。

    - 首次运行按最早日志回填全部历史（覆盖仪表盘累计值）；
    - 后续只重算缺失日期、今天，以及 `updated_at` 越过水位的日期（会话续写会更新
      `updated_at`，但统计日仍按 `created_at` 归属）；
    - 结构版本变化时整段重算一次；
    - 每天读取后在公平锁内回滚只读事务，块间给其他任务和在线查询让路。
    """
    bounds = _rollup_range(db)
    if bounds is None:
        # 空库也要落结构版本，避免每次启动都判定为待回填
        meta = db.get(DashboardStatsMeta, 1)
        if meta is None:
            meta = DashboardStatsMeta(id=1)
            db.add(meta)
        meta.rollup_schema = ROLLUP_SCHEMA
        meta.updated_at = utcnow()
        db.commit()
        return
    oldest, today = bounds
    start_key = oldest.isoformat()
    end_key = today.isoformat()
    scan_started_at = utcnow()

    meta = db.get(DashboardStatsMeta, 1)
    watermark = meta.rollup_watermark if meta is not None else None
    schema_changed = meta is None or int(meta.rollup_schema or 0) != ROLLUP_SCHEMA
    dirty: set[date] = set()
    if watermark is not None and not schema_changed:
        with STATS_SCAN_LOCK:
            try:
                changed_days = db.execute(
                    select(_DAY_EXPR)
                    .where(RequestLog.updated_at.is_not(None), RequestLog.updated_at >= watermark)
                    .distinct()
                ).all()
            finally:
                db.rollback()
        for (raw_day,) in changed_days:
            if raw_day is None:
                continue
            try:
                parsed = date.fromisoformat(str(raw_day))
            except ValueError:
                continue
            if oldest <= parsed <= today:
                dirty.add(parsed)

    existing = set(
        db.scalars(
            select(RequestLogDailyStats.day).where(
                RequestLogDailyStats.day >= start_key,
                RequestLogDailyStats.day <= end_key,
            )
        ).all()
    )
    db.rollback()

    to_compute: list[date] = []
    cursor = oldest
    while cursor <= today:
        key = cursor.isoformat()
        if schema_changed or key not in existing or cursor in dirty or cursor == today:
            to_compute.append(cursor)
        cursor += timedelta(days=1)

    failed = False
    for day in to_compute:
        try:
            with STATS_SCAN_LOCK:
                try:
                    data = _compute_day_rollup(db, day)
                finally:
                    db.rollback()
            _write_rollup(db, day, data)
        except Exception:
            db.rollback()
            failed = True
            continue
        yield_to_others()

    if failed:
        raise RuntimeError("请求日志日聚合未全部完成，本轮快照不更新")
    meta = db.get(DashboardStatsMeta, 1)
    if meta is None:
        meta = DashboardStatsMeta(id=1)
        db.add(meta)
    meta.rollup_watermark = scan_started_at
    meta.rollup_schema = ROLLUP_SCHEMA
    meta.updated_at = utcnow()
    db.commit()


def _load_history_rollups(db: Session, *, include_keys: bool = False) -> list[Any]:
    """读取「今天以前」已有的日聚合。

    缺口由后台任务补齐。页面读取不能因为缺一天就退回扫描整张请求日志。
    """
    bounds = _rollup_range(db)
    if bounds is None:
        return []
    oldest, today = bounds
    history_end = today - timedelta(days=1)
    if oldest > history_end:
        return []
    columns = [
        RequestLogDailyStats.day,
        RequestLogDailyStats.requests,
        RequestLogDailyStats.failures,
        RequestLogDailyStats.tokens,
    ]
    if include_keys:
        columns.append(RequestLogDailyStats.keys_json)
    rows = db.execute(
        select(*columns)
        .where(
            RequestLogDailyStats.day >= oldest.isoformat(),
            RequestLogDailyStats.day <= history_end.isoformat(),
        )
        .order_by(RequestLogDailyStats.day)
    ).all()
    db.rollback()
    return list(rows)
    columns = [
        RequestLogDailyStats.day,
        RequestLogDailyStats.requests,
        RequestLogDailyStats.failures,
        RequestLogDailyStats.tokens,
    ]
    if include_keys:
        columns.append(RequestLogDailyStats.keys_json)
    rows = db.execute(
        select(*columns)
        .where(
            RequestLogDailyStats.day >= oldest.isoformat(),
            RequestLogDailyStats.day <= history_end.isoformat(),
        )
        .order_by(RequestLogDailyStats.day)
    ).all()
    db.rollback()
    return list(rows)


def rollups_complete(db: Session) -> bool:
    """历史日聚合是否已按当前结构完整覆盖。"""
    meta = db.get(DashboardStatsMeta, 1)
    if meta is None or int(meta.rollup_schema or 0) != ROLLUP_SCHEMA:
        return False
    bounds = _rollup_range(db)
    if bounds is None:
        return True
    oldest, today = bounds
    history_end = today - timedelta(days=1)
    if oldest > history_end:
        return True
    expected = (history_end - oldest).days + 1
    count = db.scalar(
        select(func.count())
        .select_from(RequestLogDailyStats)
        .where(
            RequestLogDailyStats.day >= oldest.isoformat(),
            RequestLogDailyStats.day <= history_end.isoformat(),
        )
    )
    db.rollback()
    return int(count or 0) == expected


def _today_log_scalars(db: Session, today_start: datetime) -> tuple[int, int, int]:
    row = db.execute(
        select(_CALLS_EXPR, _FAIL_EXPR, _TOKEN_EXPR).where(RequestLog.created_at >= today_start)
    ).one()
    return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)


def dashboard_totals(db: Session) -> dict[str, int]:
    """仪表盘今日/累计标量。

    今天实时读取当天分区，历史读取已有日聚合。
    """
    rows = _load_history_rollups(db)
    today_requests, today_failures, today_tokens = _today_log_scalars(db, shanghai_day_start_utc())
    total_requests = sum(int(row.requests or 0) for row in rows) + today_requests
    total_tokens = sum(int(row.tokens or 0) for row in rows) + today_tokens
    return {
        "today_requests": today_requests,
        "today_failures": today_failures,
        "today_tokens": today_tokens,
        "total_requests": total_requests,
        "total_tokens": total_tokens,
    }


def key_usage_totals(db: Session, key_ids: list[int]) -> dict[int, tuple[int, int]]:
    """按 Key 的今日/累计 Token；历史走日聚合，今天实时读取。"""
    if not key_ids:
        return {}
    rows = _load_history_rollups(db, include_keys=True)
    totals: dict[int, list[int]] = {key_id: [0, 0] for key_id in key_ids}
    wanted = {str(key_id) for key_id in key_ids}
    for row in rows:
        keys = _load_json_dict(row.keys_json)
        for key in wanted:
            value = keys.get(key)
            if not isinstance(value, dict):
                continue
            totals[int(key)][1] += int(value.get("tokens") or 0)
    today_rows = db.execute(
        select(RequestLog.api_key_id, _TOKEN_EXPR)
        .where(
            RequestLog.api_key_id.in_(key_ids),
            RequestLog.created_at >= shanghai_day_start_utc(),
        )
        .group_by(RequestLog.api_key_id)
    ).all()
    for key_id, tokens in today_rows:
        if key_id in totals:
            totals[key_id][0] += int(tokens or 0)
            totals[key_id][1] += int(tokens or 0)
    db.rollback()
    return {key_id: (values[0], values[1]) for key_id, values in totals.items()}

def _load_rollups(db: Session, start: date, end: date) -> list[Any]:
    rows = db.execute(
        select(
            RequestLogDailyStats.day,
            RequestLogDailyStats.log_count,
            RequestLogDailyStats.requests,
            RequestLogDailyStats.failures,
            RequestLogDailyStats.prompt_tokens,
            RequestLogDailyStats.completion_tokens,
            RequestLogDailyStats.tokens,
            RequestLogDailyStats.latency_sum,
            RequestLogDailyStats.latency_count,
            RequestLogDailyStats.models_json,
            RequestLogDailyStats.accounts_json,
            RequestLogDailyStats.hourly_json,
            RequestLogDailyStats.latency_hist_json,
        )
        .where(
            RequestLogDailyStats.day >= start.isoformat(),
            RequestLogDailyStats.day <= end.isoformat(),
        )
        .order_by(RequestLogDailyStats.day)
    ).all()
    db.rollback()
    return list(rows)

def _summary_from_rollups(rows: list[Any]) -> DashboardStatsSummaryOut:
    requests = 0
    failures = 0
    prompt_tokens = 0
    completion_tokens = 0
    tokens = 0
    latency_sum = 0
    latency_count = 0
    active_days = 0
    models: set[str] = set()
    latency_hist: Counter[int] = Counter()
    for row in rows:
        requests += int(row.requests or 0)
        failures += int(row.failures or 0)
        prompt_tokens += int(row.prompt_tokens or 0)
        completion_tokens += int(row.completion_tokens or 0)
        tokens += int(row.tokens or 0)
        latency_sum += int(row.latency_sum or 0)
        latency_count += int(row.latency_count or 0)
        if int(row.log_count or 0) > 0:
            active_days += 1
        models.update(_load_json_dict(row.models_json))
        latency_hist.update(_load_histogram(row.latency_hist_json))
    return DashboardStatsSummaryOut(
        requests=requests,
        failures=failures,
        success_rate=round((requests - failures) / requests, 4) if requests else 0.0,
        tokens=tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        avg_latency_ms=int(round(latency_sum / latency_count)) if latency_count else None,
        p95_latency_ms=_p95_from_histogram(latency_hist),
        active_days=active_days,
        active_models=len(models),
    )


def _build_snapshot(db: Session, days: int) -> DashboardStatsOut:
    from app.routers.admin_dashboard import STATS_MODEL_SERIES, STATS_RANK_LIMIT

    today = datetime.now(SHANGHAI_TZ).date()
    start = today - timedelta(days=days - 1)
    previous_start = start - timedelta(days=days)
    previous_end = start - timedelta(days=1)
    current_rows = _load_rollups(db, start, today)
    previous_rows = _load_rollups(db, previous_start, previous_end)

    daily_totals: dict[str, dict[str, int]] = {}
    model_totals: dict[str, dict[str, int]] = {}
    daily_models: dict[str, dict[str, int]] = {}
    hourly_totals: dict[int, int] = {}
    account_totals: dict[str, dict[str, int]] = {}
    for row in current_rows:
        daily_totals[row.day] = {
            "requests": int(row.requests or 0),
            "failures": int(row.failures or 0),
            "prompt_tokens": int(row.prompt_tokens or 0),
            "completion_tokens": int(row.completion_tokens or 0),
            "tokens": int(row.tokens or 0),
        }
        day_models: dict[str, int] = {}
        for model, values in _load_json_dict(row.models_json).items():
            requests = int((values or {}).get("requests") or 0)
            tokens = int((values or {}).get("tokens") or 0)
            bucket = model_totals.setdefault(model, {"requests": 0, "tokens": 0})
            bucket["requests"] += requests
            bucket["tokens"] += tokens
            day_models[model] = requests
        daily_models[row.day] = day_models
        for account, values in _load_json_dict(row.accounts_json).items():
            requests = int((values or {}).get("requests") or 0)
            tokens = int((values or {}).get("tokens") or 0)
            bucket = account_totals.setdefault(account, {"requests": 0, "tokens": 0})
            bucket["requests"] += requests
            bucket["tokens"] += tokens
        for hour, count in _load_json_dict(row.hourly_json).items():
            try:
                hourly_totals[int(hour)] = hourly_totals.get(int(hour), 0) + int(count or 0)
            except (TypeError, ValueError):
                continue

    ranked_models = sorted(model_totals.items(), key=lambda item: (-item[1]["requests"], item[0]))
    series_models = [name for name, _ in ranked_models[:STATS_MODEL_SERIES]]
    has_other_models = len(ranked_models) > len(series_models)
    total_requests = sum(entry["requests"] for entry in daily_totals.values()) or 0

    models: list[DashboardStatsModelOut] = [
        DashboardStatsModelOut(
            model=name,
            requests=entry["requests"],
            tokens=entry["tokens"],
            request_share=round(entry["requests"] / total_requests, 4) if total_requests else 0.0,
        )
        for name, entry in ranked_models[:STATS_MODEL_SERIES]
    ]
    if has_other_models:
        other_requests = sum(entry["requests"] for _, entry in ranked_models[STATS_MODEL_SERIES:])
        other_tokens = sum(entry["tokens"] for _, entry in ranked_models[STATS_MODEL_SERIES:])
        models.append(
            DashboardStatsModelOut(
                model="其他",
                requests=other_requests,
                tokens=other_tokens,
                request_share=round(other_requests / total_requests, 4) if total_requests else 0.0,
            )
        )

    dates = [(start + timedelta(days=index)).strftime("%Y-%m-%d") for index in range(days)]
    daily: list[DashboardStatsDayOut] = []
    for day in dates:
        totals = daily_totals.get(day, {})
        bucketed: dict[str, int] = {}
        for name, count in daily_models.get(day, {}).items():
            key = name if name in series_models else "其他"
            bucketed[key] = bucketed.get(key, 0) + count
        counts: dict[str, int] = {}
        if has_other_models or series_models:
            counts = {key: bucketed[key] for key in series_models if bucketed.get(key)}
            if bucketed.get("其他"):
                counts["其他"] = bucketed["其他"]
        daily.append(
            DashboardStatsDayOut(
                date=day,
                requests=int(totals.get("requests", 0)),
                failures=int(totals.get("failures", 0)),
                prompt_tokens=int(totals.get("prompt_tokens", 0)),
                completion_tokens=int(totals.get("completion_tokens", 0)),
                tokens=int(totals.get("tokens", 0)),
                model_counts=counts,
            )
        )

    hourly = [DashboardStatsHourOut(hour=hour, requests=hourly_totals.get(hour, 0)) for hour in range(24)]
    accounts = [
        DashboardStatsAccountOut(
            account_name=name,
            requests=int(entry["requests"]),
            tokens=int(entry["tokens"]),
            request_share=round(int(entry["requests"]) / total_requests, 4) if total_requests else 0.0,
        )
        for name, entry in sorted(account_totals.items(), key=lambda item: (-item[1]["requests"], item[0]))[
            :STATS_RANK_LIMIT
        ]
    ]
    return DashboardStatsOut(
        days=days,
        start_date=dates[0],
        end_date=dates[-1],
        daily=daily,
        models=models,
        accounts=accounts,
        hourly=hourly,
        summary=_summary_from_rollups(current_rows),
        previous=_summary_from_rollups(previous_rows),
    )


def refresh_snapshot_from_rollups(db: Session, days: int) -> DashboardStatsOut:
    bounded = max(1, min(int(days), STATS_MAX_DAYS))
    stats = _build_snapshot(db, bounded)
    return _save_snapshot(db, bounded, stats)


def refresh_all(db: Session, days_list: tuple[int, ...] | None = None) -> int:
    ensure_daily_rollups(db)
    refreshed = 0
    for days in days_list or SNAPSHOT_DAYS:
        try:
            refresh_snapshot_from_rollups(db, days)
            refreshed += 1
        except Exception as error:
            db.rollback()
            record_snapshot_error(db, days, str(error))
    return refreshed

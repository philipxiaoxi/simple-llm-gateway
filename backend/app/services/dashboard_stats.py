"""概览图表快照与请求日志日聚合。

请求只读 `dashboard_stats_snapshots`。后台任务不再每轮重扫整张大表：先把请求日志按
上海自然日增量聚合成 `request_log_daily_stats`，再从日行拼出 7/14/30/90 天快照。
首轮会回填最近 180 天（覆盖 90 天区间及其环比），之后只重算有新增/续写日志的日期。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.clock import SHANGHAI_TZ, utcnow
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
# 90 天快照还要算前一个 90 天区间做环比，所以日聚合至少保留 180 天。
ROLLUP_LOOKBACK_DAYS = max(SNAPSHOT_DAYS) * 2

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
    """手动刷新：当场扫日志重算，保证接口语义与旧版本一致。"""
    from app.routers.admin_dashboard import compute_stats

    bounded = max(1, min(int(days), STATS_MAX_DAYS))
    stats = compute_stats(db, bounded)
    return _save_snapshot(db, bounded, stats)


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
    row.hourly_json = json.dumps(data["hourly"], ensure_ascii=False)
    row.latency_hist_json = json.dumps(data["latency_hist"], ensure_ascii=False)
    row.computed_at = utcnow()
    db.commit()


def ensure_daily_rollups(db: Session) -> None:
    """增量补齐最近 180 天的请求日志日聚合。

    - 首次运行回填全部 180 天；
    - 后续只重算缺失日期、今天，以及 `updated_at` 越过水位的日期（会话续写会更新
      `updated_at`，但统计日仍按 `created_at` 归属）；
    - 每天读取后在公平锁内回滚只读事务，块间给其他任务和在线查询让路。
    """
    today = datetime.now(SHANGHAI_TZ).date()
    oldest = today - timedelta(days=ROLLUP_LOOKBACK_DAYS - 1)
    start_key = oldest.isoformat()
    end_key = today.isoformat()
    scan_started_at = utcnow()

    meta = db.get(DashboardStatsMeta, 1)
    watermark = meta.rollup_watermark if meta is not None else None
    dirty: set[date] = set()
    if watermark is not None:
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
        if key not in existing or cursor in dirty or cursor == today:
            to_compute.append(cursor)
        cursor += timedelta(days=1)

    for day in to_compute:
        with STATS_SCAN_LOCK:
            try:
                data = _compute_day_rollup(db, day)
            finally:
                db.rollback()
        _write_rollup(db, day, data)
        yield_to_others()

    meta = db.get(DashboardStatsMeta, 1)
    if meta is None:
        meta = DashboardStatsMeta(id=1)
        db.add(meta)
    meta.rollup_watermark = scan_started_at
    meta.updated_at = utcnow()
    db.commit()


def _load_rollups(db: Session, start: date, end: date) -> list[RequestLogDailyStats]:
    rows = list(
        db.scalars(
            select(RequestLogDailyStats)
            .where(
                RequestLogDailyStats.day >= start.isoformat(),
                RequestLogDailyStats.day <= end.isoformat(),
            )
            .order_by(RequestLogDailyStats.day)
        ).all()
    )
    db.rollback()
    return rows


def _summary_from_rollups(rows: list[RequestLogDailyStats]) -> DashboardStatsSummaryOut:
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

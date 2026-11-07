"""MCP 调用图表快照。

与概览图表共用 dashboard_stats 定时任务刷新。请求只读 `mcp_call_stats_snapshots`，
避免打开页面时把整张调用日志表拉进内存。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import SHANGHAI_TZ, shanghai_day_start_utc, utcnow
from app.models import McpCallDailyStats, McpCallLog, McpCallStatsMeta
from app.models import McpCallStatsSnapshot as StatsRow
from app.schemas import McpCallStatsBucket, McpCallStatsOut, McpLatencyBucket
from app.services.stats_scan import STATS_SCAN_LOCK, iter_time_chunks, yield_to_others

STATS_MAX_DAYS = 90
# 概览页区间档位 + MCP 调用页的「今天」。任务每次把这些档都重算一遍。
SNAPSHOT_DAYS = (1, 7, 14, 30, 90)
# MCP 快照只看最近 90 天，日聚合保留同样窗口。
ROLLUP_LOOKBACK_DAYS = max(SNAPSHOT_DAYS)
_MCP_DAY_EXPR = func.strftime("%Y-%m-%d", McpCallLog.created_at, "+8 hours")
# 快照结构版本：字段变更时自增，旧快照会在任务启动时被判定为待刷新
SNAPSHOT_SCHEMA = 2
# 失败榜与慢操作榜最多保留几项，其余并入「其他」
FAILURE_LIMIT = 6
SLOW_LIMIT = 6
# 耗时区间（毫秒）。单一能力占绝大多数时，分布比来源更能看出性能问题
LATENCY_BUCKETS: tuple[tuple[str, str, int, int | None], ...] = (
    ("lt-100", "< 100ms", 0, 100),
    ("100-300", "100-300ms", 100, 300),
    ("300-1000", "0.3-1s", 300, 1000),
    ("1-3s", "1-3s", 1000, 3000),
    ("gte-3s", "≥ 3s", 3000, None),
)

OTHER_KEY = "other"
OTHER_LABEL = "其他"


def snapshot_days() -> tuple[int, ...]:
    return SNAPSHOT_DAYS


def snapshot_is_current(payload_json: str | None) -> bool:
    """快照是否已按当前结构生成。字段变更后旧快照会触发一次补刷新。"""
    if not payload_json or payload_json == "{}":
        return False
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("schema") == SNAPSHOT_SCHEMA


def empty_stats(days: int) -> McpCallStatsOut:
    return McpCallStatsOut(days=max(1, min(int(days), STATS_MAX_DAYS)))


def load_snapshot(db: Session, days: int) -> McpCallStatsOut | None:
    row = db.get(StatsRow, days)
    if row is None or not row.payload_json:
        return None
    try:
        payload = json.loads(row.payload_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return McpCallStatsOut.model_validate(payload)
    except Exception:
        return None


def _save_snapshot(db: Session, days: int, stats: McpCallStatsOut) -> McpCallStatsOut:
    computed_at = utcnow()
    payload = stats.model_dump(mode="json")
    payload["schema"] = SNAPSHOT_SCHEMA
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


def refresh_snapshot(db: Session, days: int) -> McpCallStatsOut:
    """手动刷新：先补齐日聚合，再从聚合拼快照，避免当场扫描整段日志。"""
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


def _p95_from_histogram(histogram: dict[int, int]) -> int:
    if not histogram:
        return 0
    total = sum(histogram.values())
    offset = min(total - 1, max(0, int(round((total - 1) * 0.95))))
    seen = 0
    for value in sorted(histogram):
        seen += int(histogram[value])
        if seen > offset:
            return int(value)
    return int(max(histogram))


def _bucket(key: str, label: str, value: dict, total_all: int) -> McpCallStatsBucket:
    total = int(value["total"] or 0)
    success = int(value["success"] or 0)
    return McpCallStatsBucket(
        key=key,
        label=label,
        total=total,
        success=success,
        failed=max(0, total - success),
        avg_latency_ms=int(round(value["latency"] / total)) if total else 0,
        p95_latency_ms=_p95_from_histogram(value.get("latency_hist") or {}),
        request_share=round(total / total_all, 4) if total_all else 0.0,
    )


def _aggregate(rows, key_of, label_of, buckets: dict[str, dict] | None = None) -> dict[str, dict]:
    buckets = buckets if buckets is not None else {}
    for row in rows:
        key = key_of(row) or "未知"
        item = buckets.setdefault(
            key,
            {
                "label": label_of(row) or key,
                "total": 0,
                "success": 0,
                "latency": 0,
                "latency_hist": Counter(),
            },
        )
        item["total"] += 1
        item["success"] += 1 if row.success else 0
        item["latency"] += int(row.latency_ms or 0)
        item["latency_hist"][int(row.latency_ms or 0)] += 1
    return buckets


def _merge_tail(result: list[McpCallStatsBucket], merged: dict, total_all: int) -> None:
    if merged["total"]:
        result.append(_bucket(OTHER_KEY, OTHER_LABEL, merged, total_all))


def _rank(
    buckets: dict[str, dict],
    total_all: int,
    limit: int,
    *,
    sort_key=None,
    merge: bool = True,
) -> list[McpCallStatsBucket]:
    """排序取前 N 项，其余合并成「其他」，避免长尾把列表和占比撑散。"""
    ranked = sorted(buckets.items(), key=sort_key or (lambda entry: (-entry[1]["total"], entry[0])))
    head, tail = ranked[:limit], ranked[limit:]
    result = [_bucket(key, value["label"], value, total_all) for key, value in head]
    if merge and tail:
        merged = {
            "label": OTHER_LABEL,
            "total": 0,
            "success": 0,
            "latency": 0,
            "latency_hist": Counter(),
        }
        for _, value in tail:
            merged["total"] += value["total"]
            merged["success"] += value["success"]
            merged["latency"] += value["latency"]
            merged["latency_hist"].update(value.get("latency_hist") or {})
        _merge_tail(result, merged, total_all)
    return result


def _empty_latency_buckets() -> list[dict]:
    return [
        {"key": key, "label": label, "total": 0, "success": 0}
        for key, label, _, _ in LATENCY_BUCKETS
    ]


def _bump_latency_bucket(buckets: list[dict], row) -> None:
    latency = int(row.latency_ms or 0)
    for index, (_, _, low, high) in enumerate(LATENCY_BUCKETS):
        if latency >= low and (high is None or latency < high):
            buckets[index]["total"] += 1
            buckets[index]["success"] += 1 if row.success else 0
            return


def _latency_bucket_out(buckets: list[dict]) -> list[McpLatencyBucket]:
    return [
        McpLatencyBucket(
            key=item["key"],
            label=item["label"],
            total=item["total"],
            success=item["success"],
            failed=max(0, item["total"] - item["success"]),
        )
        for item in buckets
    ]


def compute_stats(db: Session, days: int) -> McpCallStatsOut:
    bounded = max(1, min(int(days), STATS_MAX_DAYS))
    # 与概览图表一致，按上海自然日划分区间与分桶（库里存 naive UTC）
    since = shanghai_day_start_utc() - timedelta(days=bounded - 1)
    end = since + timedelta(days=bounded)

    total = 0
    success_count = 0
    latency_total = 0
    latency_hist: Counter[int] = Counter()
    today = datetime.now(SHANGHAI_TZ).date()
    daily_map = {
        (today - timedelta(days=offset)).isoformat(): {"total": 0, "success": 0, "latency": 0}
        for offset in range(bounded - 1, -1, -1)
    }
    hourly_map = {f"{hour:02d}": {"total": 0, "success": 0, "latency": 0} for hour in range(24)}
    operations: dict[str, dict] = {}
    latency_buckets = _empty_latency_buckets()

    # 按时间窗分块，避免一次 fetchall 把整段日志拉进内存；块间释放只读事务并让出锁。
    for chunk_start, chunk_end in iter_time_chunks(since, end):
        with STATS_SCAN_LOCK:
            try:
                rows = db.execute(
                    select(
                        McpCallLog.capability_id,
                        McpCallLog.operation,
                        McpCallLog.success,
                        McpCallLog.latency_ms,
                        McpCallLog.mcp_key_name,
                        McpCallLog.mcp_key_prefix,
                        McpCallLog.created_at,
                    ).where(
                        McpCallLog.created_at >= chunk_start,
                        McpCallLog.created_at < chunk_end,
                    )
                ).all()
            finally:
                db.rollback()

        for row in rows:
            latency = int(row.latency_ms or 0)
            total += 1
            success_count += 1 if row.success else 0
            latency_total += latency
            latency_hist[latency] += 1

            created = row.created_at + timedelta(hours=8)
            day = created.date().isoformat()
            if day in daily_map:
                daily_map[day]["total"] += 1
                daily_map[day]["success"] += 1 if row.success else 0
                daily_map[day]["latency"] += latency
            hour = f"{created.hour:02d}"
            hourly_map[hour]["total"] += 1
            hourly_map[hour]["success"] += 1 if row.success else 0
            hourly_map[hour]["latency"] += latency
            _bump_latency_bucket(latency_buckets, row)

        _aggregate(
            rows,
            lambda row: f"{row.capability_id}:{row.operation}",
            lambda row: f"{row.capability_id} / {row.operation}",
            operations,
        )
        yield_to_others()

    p95 = _p95_from_histogram(latency_hist)
    avg = int(round(latency_total / total)) if total else 0
    failing = {key: value for key, value in operations.items() if value["total"] > value["success"]}

    slow = _rank(
        operations,
        total,
        SLOW_LIMIT,
        # 先比 P95，再看调用量：少量偶发慢请求不该挤掉持续慢的操作
        sort_key=lambda entry: (
            -_p95_from_histogram(entry[1]["latency_hist"]),
            -entry[1]["total"],
            entry[0],
        ),
        merge=False,
    )
    failing_ranked = _rank(
        failing,
        total,
        FAILURE_LIMIT,
        sort_key=lambda entry: (
            -(entry[1]["total"] - entry[1]["success"]),
            -entry[1]["total"],
            entry[0],
        ),
    )

    return McpCallStatsOut(
        days=bounded,
        total=total,
        success=success_count,
        failed=total - success_count,
        success_rate=round(success_count / total, 4) if total else 0,
        avg_latency_ms=avg,
        p95_latency_ms=p95,
        latency_buckets=_latency_bucket_out(latency_buckets),
        slow_operations=slow,
        failure_operations=failing_ranked,
        daily=[
            _bucket(key, key[5:], value, total)
            for key, value in daily_map.items()
        ],
        hourly=[
            _bucket(key, f"{key}:00", value, total)
            for key, value in hourly_map.items()
        ],
    )


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


def _load_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _load_histogram(raw: str | None) -> Counter[int]:
    histogram: Counter[int] = Counter()
    for key, value in _load_json_dict(raw).items():
        try:
            histogram[int(key)] += int(value)
        except (TypeError, ValueError):
            continue
    return histogram


def _compute_day_rollup(db: Session, day: date) -> dict[str, Any]:
    start = _day_start_utc(day)
    end = _day_start_utc(day + timedelta(days=1))
    rows = db.execute(
        select(
            McpCallLog.capability_id,
            McpCallLog.operation,
            McpCallLog.success,
            McpCallLog.latency_ms,
            McpCallLog.created_at,
        ).where(
            McpCallLog.created_at >= start,
            McpCallLog.created_at < end,
        )
    ).all()

    total = 0
    success_count = 0
    latency_sum = 0
    latency_hist: Counter[int] = Counter()
    latency_buckets = _empty_latency_buckets()
    hourly: dict[str, dict[str, int]] = {}
    operations: dict[str, dict] = {}
    for row in rows:
        latency = int(row.latency_ms or 0)
        total += 1
        success_count += 1 if row.success else 0
        latency_sum += latency
        latency_hist[latency] += 1
        _bump_latency_bucket(latency_buckets, row)

        created = row.created_at + timedelta(hours=8)
        hour = f"{created.hour:02d}"
        hour_bucket = hourly.setdefault(hour, {"total": 0, "success": 0, "latency": 0})
        hour_bucket["total"] += 1
        hour_bucket["success"] += 1 if row.success else 0
        hour_bucket["latency"] += latency

        key = f"{row.capability_id}:{row.operation}"
        operation = operations.setdefault(
            key,
            {
                "label": f"{row.capability_id} / {row.operation}",
                "total": 0,
                "success": 0,
                "latency": 0,
                "latency_hist": Counter(),
            },
        )
        operation["total"] += 1
        operation["success"] += 1 if row.success else 0
        operation["latency"] += latency
        operation["latency_hist"][latency] += 1

    return {
        "total": total,
        "success": success_count,
        "latency_sum": latency_sum,
        "hourly": hourly,
        "operations": {
            key: {**value, "latency_hist": dict(value["latency_hist"])}
            for key, value in operations.items()
        },
        "latency_hist": dict(latency_hist),
        "latency_buckets": latency_buckets,
    }


def _write_rollup(db: Session, day: date, data: dict[str, Any]) -> None:
    key = day.isoformat()
    row = db.get(McpCallDailyStats, key)
    if row is None:
        row = McpCallDailyStats(day=key)
        db.add(row)
    row.total = int(data["total"])
    row.success = int(data["success"])
    row.latency_sum = int(data["latency_sum"])
    row.hourly_json = json.dumps(data["hourly"], ensure_ascii=False)
    row.operations_json = json.dumps(data["operations"], ensure_ascii=False)
    row.latency_hist_json = json.dumps(data["latency_hist"], ensure_ascii=False)
    row.latency_buckets_json = json.dumps(data["latency_buckets"], ensure_ascii=False)
    row.computed_at = utcnow()
    db.commit()


def ensure_daily_rollups(db: Session) -> None:
    """增量补齐最近 90 天的 MCP 调用日聚合。

    MCP 日志只增不改；水位用 created_at，新日志落在水位之后就会被重算。
    删除由保留期清理负责同步清理对应的日聚合行。
    """
    today = datetime.now(SHANGHAI_TZ).date()
    oldest = today - timedelta(days=ROLLUP_LOOKBACK_DAYS - 1)
    start_key = oldest.isoformat()
    end_key = today.isoformat()
    scan_started_at = utcnow()

    meta = db.get(McpCallStatsMeta, 1)
    watermark = meta.rollup_watermark if meta is not None else None
    dirty: set[date] = set()
    if watermark is not None:
        with STATS_SCAN_LOCK:
            try:
                changed_days = db.execute(
                    select(_MCP_DAY_EXPR).where(McpCallLog.created_at >= watermark).distinct()
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
            select(McpCallDailyStats.day).where(
                McpCallDailyStats.day >= start_key,
                McpCallDailyStats.day <= end_key,
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

    meta = db.get(McpCallStatsMeta, 1)
    if meta is None:
        meta = McpCallStatsMeta(id=1)
        db.add(meta)
    meta.rollup_watermark = scan_started_at
    meta.updated_at = utcnow()
    db.commit()


def _load_rollups(db: Session, start: date, end: date) -> list[Any]:
    rows = db.execute(
        select(
            McpCallDailyStats.day,
            McpCallDailyStats.total,
            McpCallDailyStats.success,
            McpCallDailyStats.latency_sum,
            McpCallDailyStats.hourly_json,
            McpCallDailyStats.operations_json,
            McpCallDailyStats.latency_hist_json,
            McpCallDailyStats.latency_buckets_json,
        )
        .where(
            McpCallDailyStats.day >= start.isoformat(),
            McpCallDailyStats.day <= end.isoformat(),
        )
        .order_by(McpCallDailyStats.day)
    ).all()
    db.rollback()
    return list(rows)


def _merge_operations(target: dict[str, dict], raw: str | None) -> None:
    for key, value in _load_json_dict(raw).items():
        if not isinstance(value, dict):
            continue
        item = target.setdefault(
            key,
            {
                "label": str(value.get("label") or key),
                "total": 0,
                "success": 0,
                "latency": 0,
                "latency_hist": Counter(),
            },
        )
        item["total"] += int(value.get("total") or 0)
        item["success"] += int(value.get("success") or 0)
        item["latency"] += int(value.get("latency") or 0)
        item["latency_hist"].update(_load_histogram(json.dumps(value.get("latency_hist") or {})))


def _build_snapshot_from_rollups(db: Session, days: int) -> McpCallStatsOut:
    today = datetime.now(SHANGHAI_TZ).date()
    start = today - timedelta(days=days - 1)
    rows = _load_rollups(db, start, today)
    by_day = {row.day: row for row in rows}

    total = 0
    success_count = 0
    latency_sum = 0
    latency_hist: Counter[int] = Counter()
    latency_buckets = _empty_latency_buckets()
    hourly_map = {f"{hour:02d}": {"total": 0, "success": 0, "latency": 0} for hour in range(24)}
    operations: dict[str, dict] = {}
    for row in rows:
        total += int(row.total or 0)
        success_count += int(row.success or 0)
        latency_sum += int(row.latency_sum or 0)
        latency_hist.update(_load_histogram(row.latency_hist_json))
        for hour, value in _load_json_dict(row.hourly_json).items():
            if hour not in hourly_map or not isinstance(value, dict):
                continue
            hourly_map[hour]["total"] += int(value.get("total") or 0)
            hourly_map[hour]["success"] += int(value.get("success") or 0)
            hourly_map[hour]["latency"] += int(value.get("latency") or 0)
        for index, value in enumerate(_load_json_list(row.latency_buckets_json)):
            if index >= len(latency_buckets) or not isinstance(value, dict):
                continue
            latency_buckets[index]["total"] += int(value.get("total") or 0)
            latency_buckets[index]["success"] += int(value.get("success") or 0)
        _merge_operations(operations, row.operations_json)

    daily: list[McpCallStatsBucket] = []
    for offset in range(days - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        row = by_day.get(day)
        value = {
            "total": int(row.total or 0) if row is not None else 0,
            "success": int(row.success or 0) if row is not None else 0,
            "latency": int(row.latency_sum or 0) if row is not None else 0,
        }
        daily.append(_bucket(day, day[5:], value, total))

    failing = {key: value for key, value in operations.items() if value["total"] > value["success"]}
    slow = _rank(
        operations,
        total,
        SLOW_LIMIT,
        sort_key=lambda entry: (
            -_p95_from_histogram(entry[1]["latency_hist"]),
            -entry[1]["total"],
            entry[0],
        ),
        merge=False,
    )
    failing_ranked = _rank(
        failing,
        total,
        FAILURE_LIMIT,
        sort_key=lambda entry: (
            -(entry[1]["total"] - entry[1]["success"]),
            -entry[1]["total"],
            entry[0],
        ),
    )
    return McpCallStatsOut(
        days=days,
        total=total,
        success=success_count,
        failed=total - success_count,
        success_rate=round(success_count / total, 4) if total else 0,
        avg_latency_ms=int(round(latency_sum / total)) if total else 0,
        p95_latency_ms=_p95_from_histogram(latency_hist),
        latency_buckets=_latency_bucket_out(latency_buckets),
        slow_operations=slow,
        failure_operations=failing_ranked,
        daily=daily,
        hourly=[_bucket(key, f"{key}:00", value, total) for key, value in hourly_map.items()],
    )


def refresh_snapshot_from_rollups(db: Session, days: int) -> McpCallStatsOut:
    bounded = max(1, min(int(days), STATS_MAX_DAYS))
    stats = _build_snapshot_from_rollups(db, bounded)
    return _save_snapshot(db, bounded, stats)

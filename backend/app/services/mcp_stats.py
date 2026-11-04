"""MCP 调用图表快照。

与概览图表共用 dashboard_stats 定时任务刷新。请求只读 `mcp_call_stats_snapshots`，
避免打开页面时把整张调用日志表拉进内存。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import SHANGHAI_TZ, shanghai_day_start_utc, utcnow
from app.models import McpCallLog
from app.models import McpCallStatsSnapshot as StatsRow
from app.schemas import McpCallStatsBucket, McpCallStatsOut, McpLatencyBucket

STATS_MAX_DAYS = 90
# 概览页区间档位 + MCP 调用页的「今天」。任务每次把这些档都重算一遍。
SNAPSHOT_DAYS = (1, 7, 14, 30, 90)
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


def refresh_snapshot(db: Session, days: int) -> McpCallStatsOut:
    bounded = max(1, min(int(days), STATS_MAX_DAYS))
    stats = compute_stats(db, bounded)
    computed_at = utcnow()
    payload = stats.model_dump(mode="json")
    payload["schema"] = SNAPSHOT_SCHEMA
    row = db.get(StatsRow, bounded)
    encoded = json.dumps(payload, ensure_ascii=False)
    if row is None:
        db.add(
            StatsRow(
                days=bounded,
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
    refreshed = 0
    for days in days_list or SNAPSHOT_DAYS:
        try:
            refresh_snapshot(db, days)
            refreshed += 1
        except Exception as error:
            db.rollback()
            record_snapshot_error(db, days, str(error))
    return refreshed


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * ratio)))]


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
        p95_latency_ms=_percentile(value.get("latencies") or [], 0.95),
        request_share=round(total / total_all, 4) if total_all else 0.0,
    )


def _aggregate(rows, key_of, label_of) -> dict[str, dict]:
    buckets: dict[str, dict] = {}
    for row in rows:
        key = key_of(row) or "未知"
        item = buckets.setdefault(
            key,
            {"label": label_of(row) or key, "total": 0, "success": 0, "latency": 0, "latencies": []},
        )
        item["total"] += 1
        item["success"] += 1 if row.success else 0
        item["latency"] += row.latency_ms
        item["latencies"].append(row.latency_ms)
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
        merged = {"label": OTHER_LABEL, "total": 0, "success": 0, "latency": 0, "latencies": []}
        for _, value in tail:
            merged["total"] += value["total"]
            merged["success"] += value["success"]
            merged["latency"] += value["latency"]
            merged["latencies"].extend(value["latencies"])
        _merge_tail(result, merged, total_all)
    return result


def _latency_buckets(rows) -> list[McpLatencyBucket]:
    buckets = [
        {"key": key, "label": label, "total": 0, "success": 0}
        for key, label, _, _ in LATENCY_BUCKETS
    ]
    for row in rows:
        latency = int(row.latency_ms or 0)
        for index, (_, _, low, high) in enumerate(LATENCY_BUCKETS):
            if latency >= low and (high is None or latency < high):
                buckets[index]["total"] += 1
                buckets[index]["success"] += 1 if row.success else 0
                break
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
    # 只取聚合需要的列：request_body / response_body 是 Text，全量拉取会明显抬高内存
    rows = db.execute(
        select(
            McpCallLog.capability_id,
            McpCallLog.operation,
            McpCallLog.success,
            McpCallLog.latency_ms,
            McpCallLog.mcp_key_name,
            McpCallLog.mcp_key_prefix,
            McpCallLog.created_at,
        ).where(McpCallLog.created_at >= since)
    ).all()

    total = len(rows)
    success_count = sum(1 for row in rows if row.success)
    latencies = [row.latency_ms for row in rows]
    p95 = _percentile(latencies, 0.95)
    avg = int(round(sum(latencies) / len(latencies))) if latencies else 0

    today = datetime.now(SHANGHAI_TZ).date()
    daily_map = {
        (today - timedelta(days=offset)).isoformat(): {"total": 0, "success": 0, "latency": 0}
        for offset in range(bounded - 1, -1, -1)
    }
    hourly_map = {f"{hour:02d}": {"total": 0, "success": 0, "latency": 0} for hour in range(24)}
    for row in rows:
        created = row.created_at + timedelta(hours=8)
        day = created.date().isoformat()
        if day in daily_map:
            daily_map[day]["total"] += 1
            daily_map[day]["success"] += 1 if row.success else 0
            daily_map[day]["latency"] += row.latency_ms
        hour = f"{created.hour:02d}"
        hourly_map[hour]["total"] += 1
        hourly_map[hour]["success"] += 1 if row.success else 0
        hourly_map[hour]["latency"] += row.latency_ms

    # 调用来源（能力/Key）在实际使用中高度集中，参考价值有限，
    # 这里只保留「按操作」维度，同时用于失败榜和慢操作榜。
    operations = _aggregate(
        rows,
        lambda row: f"{row.capability_id}:{row.operation}",
        lambda row: f"{row.capability_id} / {row.operation}",
    )
    failing = {key: value for key, value in operations.items() if value["total"] > value["success"]}

    slow = _rank(
        operations,
        total,
        SLOW_LIMIT,
        # 先比 P95，再看调用量：少量偶发慢请求不该挤掉持续慢的操作
        sort_key=lambda entry: (
            -_percentile(entry[1]["latencies"], 0.95),
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
        latency_buckets=_latency_buckets(rows),
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

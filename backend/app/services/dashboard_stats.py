"""概览图表快照。

请求只读 `dashboard_stats_snapshots`，聚合在后台任务里算，避免打开页面时扫大表。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models import DashboardStatsSnapshot as StatsRow
STATS_MAX_DAYS = 90
from app.schemas import DashboardStatsOut

# 前端区间档位。任务每次把这些档都重算一遍，切区间不用等。
SNAPSHOT_DAYS = (7, 14, 30, 90)


def snapshot_days() -> tuple[int, ...]:
    return SNAPSHOT_DAYS


def empty_stats(days: int) -> DashboardStatsOut:
    from app.clock import SHANGHAI_TZ

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


def refresh_snapshot(db: Session, days: int) -> DashboardStatsOut:
    from app.routers.admin_dashboard import compute_stats

    bounded = max(1, min(int(days), STATS_MAX_DAYS))
    stats = compute_stats(db, bounded)
    computed_at = utcnow()
    stats.computed_at = computed_at
    payload = stats.model_dump(mode="json")
    payload.pop("computed_at", None)
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

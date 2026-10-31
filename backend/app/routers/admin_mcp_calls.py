from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_admin
from app.models import McpCallLog
from app.schemas import (
    McpCallLogDetailOut,
    McpCallLogListOut,
    McpCallLogOut,
    McpCallStatsBucket,
    McpCallStatsOut,
)

router = APIRouter(
    prefix="/api/admin/mcp/calls",
    tags=["admin-mcp-calls"],
    dependencies=[Depends(get_current_admin)],
)


def _base(item: McpCallLog) -> dict:
    return {
        "id": item.id,
        "created_at": item.created_at,
        "mcp_key_id": item.mcp_key_id,
        "mcp_key_name": item.mcp_key_name,
        "mcp_key_prefix": item.mcp_key_prefix,
        "capability_id": item.capability_id,
        "operation": item.operation,
        "success": item.success,
        "latency_ms": item.latency_ms,
        "error_message": item.error_message,
        "error_type": item.error_type,
        "transport": item.transport,
        "http_status": item.http_status,
        "client_ip": item.client_ip,
        "client_name": item.client_name,
    }


@router.get("", response_model=McpCallLogListOut)
def list_calls(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    capability_id: str | None = None,
    mcp_key_id: int | None = None,
    success: bool | None = None,
    q: str | None = None,
):
    filters = []
    if capability_id:
        filters.append(McpCallLog.capability_id == capability_id)
    if mcp_key_id is not None:
        filters.append(McpCallLog.mcp_key_id == mcp_key_id)
    if success is not None:
        filters.append(McpCallLog.success.is_(success))
    keyword = (q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        filters.append(
            or_(
                McpCallLog.operation.like(like),
                McpCallLog.mcp_key_name.like(like),
                McpCallLog.mcp_key_prefix.like(like),
                McpCallLog.error_message.like(like),
            )
        )

    total = db.scalar(select(func.count()).select_from(McpCallLog).where(*filters)) or 0
    items = db.scalars(
        select(McpCallLog).where(*filters).order_by(McpCallLog.id.desc()).offset(offset).limit(limit)
    ).all()
    return McpCallLogListOut(
        items=[McpCallLogOut(**_base(item)) for item in items],
        total=int(total),
        limit=limit,
        offset=offset,
    )


def _filters(
    *,
    capability_id: str | None,
    mcp_key_id: int | None,
    success: bool | None,
    q: str | None,
) -> list:
    filters = []
    if capability_id:
        filters.append(McpCallLog.capability_id == capability_id)
    if mcp_key_id is not None:
        filters.append(McpCallLog.mcp_key_id == mcp_key_id)
    if success is not None:
        filters.append(McpCallLog.success.is_(success))
    keyword = (q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        filters.append(
            or_(
                McpCallLog.operation.like(like),
                McpCallLog.mcp_key_name.like(like),
                McpCallLog.mcp_key_prefix.like(like),
                McpCallLog.error_message.like(like),
            )
        )
    return filters


def _bucket(key: str, label: str, total: int, success: int, avg_latency: float | None) -> McpCallStatsBucket:
    total = int(total or 0)
    success = int(success or 0)
    return McpCallStatsBucket(
        key=key,
        label=label,
        total=total,
        success=success,
        failed=max(0, total - success),
        avg_latency_ms=int(round(avg_latency or 0)),
    )


@router.get("/summary", response_model=McpCallStatsOut)
def call_stats(
    db: Session = Depends(get_db),
    days: int = Query(default=7, ge=1, le=90),
    capability_id: str | None = None,
    mcp_key_id: int | None = None,
    success: bool | None = None,
    q: str | None = None,
):
    """按当前筛选统计调用次数、成功率、延迟与趋势。趋势按本地日期分桶。"""
    filters = _filters(capability_id=capability_id, mcp_key_id=mcp_key_id, success=success, q=q)
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days - 1)
    filters.append(McpCallLog.created_at >= since.replace(hour=0, minute=0, second=0, microsecond=0))
    rows = db.scalars(select(McpCallLog).where(*filters)).all()

    total = len(rows)
    success_count = sum(1 for row in rows if row.success)
    latencies = sorted(row.latency_ms for row in rows)
    p95 = latencies[min(len(latencies) - 1, int(round((len(latencies) - 1) * 0.95)))] if latencies else 0
    avg = int(round(sum(latencies) / len(latencies))) if latencies else 0

    def grouped(key_of, label_of, limit: int) -> list[McpCallStatsBucket]:
        buckets: dict[str, dict] = {}
        for row in rows:
            key = key_of(row) or "未知"
            item = buckets.setdefault(key, {"label": label_of(row) or key, "total": 0, "success": 0, "latency": 0})
            item["total"] += 1
            item["success"] += 1 if row.success else 0
            item["latency"] += row.latency_ms
        ranked = sorted(buckets.items(), key=lambda entry: (-entry[1]["total"], entry[0]))[:limit]
        return [
            _bucket(key, value["label"], value["total"], value["success"], value["latency"] / value["total"])
            for key, value in ranked
        ]

    today = datetime.now().date()
    daily_map = {
        (today - timedelta(days=offset)).isoformat(): {"total": 0, "success": 0, "latency": 0}
        for offset in range(days - 1, -1, -1)
    }
    hourly_map = {f"{hour:02d}": {"total": 0, "success": 0, "latency": 0} for hour in range(24)}
    for row in rows:
        created = row.created_at
        day = created.date().isoformat()
        if day in daily_map:
            daily_map[day]["total"] += 1
            daily_map[day]["success"] += 1 if row.success else 0
            daily_map[day]["latency"] += row.latency_ms
        hour = f"{created.hour:02d}"
        hourly_map[hour]["total"] += 1
        hourly_map[hour]["success"] += 1 if row.success else 0
        hourly_map[hour]["latency"] += row.latency_ms

    def series(source: dict[str, dict], label_of) -> list[McpCallStatsBucket]:
        return [
            _bucket(
                key,
                label_of(key),
                value["total"],
                value["success"],
                value["latency"] / value["total"] if value["total"] else 0,
            )
            for key, value in source.items()
        ]

    return McpCallStatsOut(
        days=days,
        total=total,
        success=success_count,
        failed=total - success_count,
        success_rate=round(success_count / total, 4) if total else 0,
        avg_latency_ms=avg,
        p95_latency_ms=p95,
        capabilities=grouped(lambda row: row.capability_id, lambda row: row.capability_id, 8),
        operations=grouped(
            lambda row: f"{row.capability_id}:{row.operation}", lambda row: f"{row.capability_id} / {row.operation}", 8
        ),
        keys=grouped(
            lambda row: row.mcp_key_name or row.mcp_key_prefix or "未知",
            lambda row: row.mcp_key_name or row.mcp_key_prefix or "未知",
            8,
        ),
        daily=series(daily_map, lambda key: key[5:]),
        hourly=series(hourly_map, lambda key: f"{key}:00"),
    )


@router.get("/{call_id}", response_model=McpCallLogDetailOut)
def get_call(call_id: int, db: Session = Depends(get_db)):
    item = db.get(McpCallLog, call_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"type": "not_found", "message": "调用记录不存在"}},
        )
    return McpCallLogDetailOut(
        **_base(item),
        user_agent=item.user_agent,
        request_headers=item.request_headers,
        request_body=item.request_body,
        response_body=item.response_body,
    )

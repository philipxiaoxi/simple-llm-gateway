from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_admin
from app.models import McpCallLog
from app.schemas import McpCallLogDetailOut, McpCallLogListOut, McpCallLogOut

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

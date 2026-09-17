from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_admin
from app.models import McpCallLog
from app.schemas import McpCallLogListOut, McpCallLogOut

router = APIRouter(
    prefix="/api/admin/mcp/calls",
    tags=["admin-mcp-calls"],
    dependencies=[Depends(get_current_admin)],
)


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
        items=[
            McpCallLogOut(
                id=item.id,
                created_at=item.created_at,
                mcp_key_id=item.mcp_key_id,
                mcp_key_name=item.mcp_key_name,
                mcp_key_prefix=item.mcp_key_prefix,
                capability_id=item.capability_id,
                operation=item.operation,
                success=item.success,
                latency_ms=item.latency_ms,
                error_message=item.error_message,
            )
            for item in items
        ],
        total=int(total),
        limit=limit,
        offset=offset,
    )

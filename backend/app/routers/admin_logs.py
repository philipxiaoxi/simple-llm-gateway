from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.deps import get_current_admin
from app.models import RequestLog, RequestLogMessage
from app.schemas import LogListOut, LogMessageListOut, LogMessageOut, LogOut
from app.serializers import log_to_out
from app.services.conversation import decode_stored_message

router = APIRouter(prefix="/api/admin/logs", tags=["admin-logs"], dependencies=[Depends(get_current_admin)])


@router.get("", response_model=LogListOut)
def list_logs(
    account_id: int | None = None,
    api_key_id: int | None = None,
    protocol: str | None = None,
    status: str | None = None,
    model: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> LogListOut:
    filters = select(RequestLog)
    if account_id is not None:
        filters = filters.where(RequestLog.account_id == account_id)
    if api_key_id is not None:
        filters = filters.where(RequestLog.api_key_id == api_key_id)
    if protocol:
        filters = filters.where(RequestLog.protocol == protocol)
    if status:
        filters = filters.where(RequestLog.status == status)
    if model:
        filters = filters.where(RequestLog.model == model)
    if since:
        filters = filters.where(RequestLog.created_at >= since)
    if until:
        filters = filters.where(RequestLog.created_at <= until)
    total = db.scalar(select(func.count()).select_from(filters.subquery())) or 0
    offset = (page - 1) * page_size
    rows = db.scalars(
        filters.options(joinedload(RequestLog.api_key), joinedload(RequestLog.account))
        .order_by(func.coalesce(RequestLog.updated_at, RequestLog.created_at).desc(), RequestLog.id.desc())
        .offset(offset)
        .limit(page_size)
    ).unique().all()
    return LogListOut(items=[log_to_out(row) for row in rows], total=total, page=page, page_size=page_size)


@router.get("/{log_id}/messages", response_model=LogMessageListOut)
def list_log_messages(
    log_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> LogMessageListOut:
    item = db.get(RequestLog, log_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    total = db.scalar(select(func.count()).where(RequestLogMessage.log_id == log_id)) or 0
    offset = (page - 1) * page_size
    rows = db.scalars(
        select(RequestLogMessage)
        .where(RequestLogMessage.log_id == log_id)
        .order_by(RequestLogMessage.seq.desc())
        .offset(offset)
        .limit(page_size)
    ).all()
    items: list[LogMessageOut] = []
    for row in rows:
        decoded = decode_stored_message(row)
        items.append(
            LogMessageOut(
                role=decoded["role"],
                content=decoded.get("content"),
                tool_calls=decoded.get("tool_calls"),
            )
        )
    return LogMessageListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/{log_id}", response_model=LogOut)
def get_log(
    log_id: int,
    include_bodies: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> LogOut:
    item = db.get(RequestLog, log_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return log_to_out(item, include_bodies=include_bodies)

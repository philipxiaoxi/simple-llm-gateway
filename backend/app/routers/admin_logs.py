from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_admin
from app.models import RequestLog
from app.schemas import LogListOut, LogOut
from app.serializers import log_to_out

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
        filters.order_by(func.coalesce(RequestLog.updated_at, RequestLog.created_at).desc(), RequestLog.id.desc())
        .offset(offset)
        .limit(page_size)
    ).all()
    return LogListOut(items=[log_to_out(row) for row in rows], total=total, page=page, page_size=page_size)


@router.get("/{log_id}", response_model=LogOut)
def get_log(log_id: int, db: Session = Depends(get_db)) -> LogOut:
    item = db.get(RequestLog, log_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return log_to_out(item, include_bodies=True)

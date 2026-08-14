from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_admin
from app.models import RequestLog
from app.schemas import LogOut
from app.serializers import log_to_out

router = APIRouter(prefix="/api/admin/logs", tags=["admin-logs"], dependencies=[Depends(get_current_admin)])


@router.get("", response_model=list[LogOut])
def list_logs(
    account_id: int | None = None,
    api_key_id: int | None = None,
    protocol: str | None = None,
    status: str | None = None,
    model: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
) -> list[LogOut]:
    statement = select(RequestLog).order_by(RequestLog.id.desc()).limit(limit)
    if account_id is not None:
        statement = statement.where(RequestLog.account_id == account_id)
    if api_key_id is not None:
        statement = statement.where(RequestLog.api_key_id == api_key_id)
    if protocol:
        statement = statement.where(RequestLog.protocol == protocol)
    if status:
        statement = statement.where(RequestLog.status == status)
    if model:
        statement = statement.where(RequestLog.model == model)
    if since:
        statement = statement.where(RequestLog.created_at >= since)
    if until:
        statement = statement.where(RequestLog.created_at <= until)
    return [log_to_out(row) for row in db.scalars(statement).all()]


@router.get("/{log_id}", response_model=LogOut)
def get_log(log_id: int, db: Session = Depends(get_db)) -> LogOut:
    item = db.get(RequestLog, log_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return log_to_out(item, include_bodies=True)

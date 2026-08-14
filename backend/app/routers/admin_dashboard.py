from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_admin
from app.models import OAuthToken, RequestLog, UpstreamAccount
from app.schemas import DashboardOut

router = APIRouter(prefix="/api/admin", tags=["admin-dashboard"], dependencies=[Depends(get_current_admin)])


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db)) -> DashboardOut:
    account_count = db.scalar(select(func.count()).select_from(UpstreamAccount)) or 0
    probe_failed = (
        db.scalar(
            select(func.count()).select_from(UpstreamAccount).where(UpstreamAccount.last_probe_ok.is_(False))
        )
        or 0
    )
    grok_missing = (
        db.scalar(
            select(func.count())
            .select_from(UpstreamAccount)
            .outerjoin(OAuthToken, OAuthToken.account_id == UpstreamAccount.id)
            .where(UpstreamAccount.provider == "grok", OAuthToken.id.is_(None))
        )
        or 0
    )
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_requests = (
        db.scalar(select(func.count()).select_from(RequestLog).where(RequestLog.created_at >= today)) or 0
    )
    today_failures = (
        db.scalar(
            select(func.count())
            .select_from(RequestLog)
            .where(RequestLog.created_at >= today, RequestLog.status == "error")
        )
        or 0
    )
    return DashboardOut(
        account_count=account_count,
        unhealthy_count=probe_failed + grok_missing,
        today_requests=today_requests,
        today_failures=today_failures,
    )

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.clock import utcnow
from app.config import get_settings
from app.db import get_session_factory
from app.models import UpstreamAccount
from app.providers import get_provider
from app.services.credentials import get_upstream_credential


async def refresh_quota(account: UpstreamAccount) -> dict[str, Any]:
    return await get_provider(account.provider).fetch_quota(account)


def accounts_due_for_quota_refresh(
    db: Session,
    *,
    now: datetime | None = None,
    interval_seconds: int | None = None,
) -> list[UpstreamAccount]:
    if interval_seconds is None:
        from app.services.job_settings import get_job_int

        interval_seconds = get_job_int(
            "quota",
            "interval_seconds",
            get_settings().quota_refresh_interval_seconds,
        )
    interval = interval_seconds
    cutoff = (now or utcnow()) - timedelta(seconds=interval)
    rows = db.scalars(
        select(UpstreamAccount)
        .options(joinedload(UpstreamAccount.oauth_token))
        .where(UpstreamAccount.status == "active")
    ).all()
    due: list[UpstreamAccount] = []
    for account in rows:
        if not get_upstream_credential(account, allow_expired=True):
            continue
        if account.quota_updated_at is None or account.quota_updated_at <= cutoff:
            due.append(account)
    return due


async def refresh_due_quotas() -> int:
    session = get_session_factory()()
    try:
        account_ids = [account.id for account in accounts_due_for_quota_refresh(session)]
    finally:
        session.close()
    refreshed = 0
    for account_id in account_ids:
        session = get_session_factory()()
        try:
            account = session.scalar(
                select(UpstreamAccount)
                .options(joinedload(UpstreamAccount.oauth_token))
                .where(UpstreamAccount.id == account_id)
            )
            if account is None:
                continue
            await refresh_quota(account)
            session.commit()
            refreshed += 1
        except Exception:
            session.rollback()
        finally:
            session.close()
    return refreshed


def quota_account_stats(db: Session) -> dict[str, Any]:
    rows = db.scalars(
        select(UpstreamAccount).where(UpstreamAccount.status == "active")
    ).all()
    usable = [account for account in rows if get_upstream_credential(account, allow_expired=True)]
    timestamps = [account.quota_updated_at for account in usable if account.quota_updated_at is not None]
    return {
        "account_count": len(usable),
        "oldest_quota_at": min(timestamps) if timestamps else None,
        "newest_quota_at": max(timestamps) if timestamps else None,
    }

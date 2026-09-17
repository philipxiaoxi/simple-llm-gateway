from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import UpstreamAccount

REEMBED_STATUSES = ("stale", "failed")


def new_id() -> str:
    return str(uuid.uuid4())


async def emit(on_progress, **payload: Any) -> None:
    """把进度推给回调；回调可为同步或异步。"""
    if on_progress is None:
        return
    result = on_progress(payload)
    if hasattr(result, "__await__"):
        await result


def account_name(db: Session, account_id: int | None) -> str | None:
    if not account_id:
        return None
    account = db.get(UpstreamAccount, account_id)
    return account.name if account else None

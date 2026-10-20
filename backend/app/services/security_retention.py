from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import delete

from app.clock import utcnow
from app.config import get_settings
from app.db import get_session_factory
from app.models import SecurityEvent

CLEANUP_INTERVAL_SECONDS = 24 * 3600


def cleanup_once() -> int:
    """删除早于保留期的安全事件，返回删除条数。"""
    days = get_settings().security_event_retention_days
    if days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=days)
    session = get_session_factory()()
    try:
        result = session.execute(delete(SecurityEvent).where(SecurityEvent.created_at < cutoff))
        session.commit()
        return int(result.rowcount or 0)
    finally:
        session.close()


async def retention_loop() -> None:
    while True:
        try:
            removed = await asyncio.to_thread(cleanup_once)
            if removed:
                print(f"[security-retention] removed {removed} events")
        except Exception as error:  # noqa: BLE001
            print(f"[security-retention] cleanup failed: {error}")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)

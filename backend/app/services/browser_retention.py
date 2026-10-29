from __future__ import annotations

import asyncio

from app.capabilities.browser import manager, screenshots
from app.db import get_session_factory

_INTERVAL_SECONDS = 60


async def retention_loop() -> None:
    """回收空闲浏览器会话，并清理过期截图。"""
    while True:
        try:
            session = get_session_factory()()
            try:
                await manager.cleanup_idle(session)
                screenshots.cleanup_expired(session)
                session.commit()
            finally:
                session.close()
        except Exception as error:  # noqa: BLE001
            print(f"[browser-retention] cleanup failed: {error}")
        await asyncio.sleep(_INTERVAL_SECONDS)

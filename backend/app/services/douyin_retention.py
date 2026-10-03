from __future__ import annotations

import asyncio

from app.capabilities.douyin import jobs, storage
from app.db import get_session_factory

CLEANUP_INTERVAL_SECONDS = 24 * 3600


def cleanup_once() -> dict[str, int]:
    session = get_session_factory()()
    try:
        purged = jobs.cleanup_expired(session)
        temp = storage.cleanup_temp()
        session.commit()
        return {"jobs": purged, "temp": temp}
    finally:
        session.close()


async def retention_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(cleanup_once)
        except Exception as error:  # noqa: BLE001
            print(f"[douyin-retention] cleanup failed: {error}")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)

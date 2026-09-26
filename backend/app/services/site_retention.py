from __future__ import annotations

import asyncio

from app.capabilities.site import sites as service
from app.capabilities.site import storage
from app.db import get_session_factory

CLEANUP_INTERVAL_SECONDS = 24 * 3600


def cleanup_once() -> dict[str, int]:
    session = get_session_factory()()
    try:
        removed = service.cleanup_expired(session)
        temp_removed = storage.cleanup_temp()
        session.commit()
        return {"versions": removed, "temp": temp_removed}
    finally:
        session.close()


async def retention_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(cleanup_once)
        except Exception as error:  # noqa: BLE001
            print(f"[site-retention] cleanup failed: {error}")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)

from __future__ import annotations

import asyncio

from app.capabilities.docparse.jobs import cleanup_expired
from app.db import get_session_factory

CLEANUP_INTERVAL_SECONDS = 24 * 3600


def cleanup_once() -> dict[str, int]:
    session = get_session_factory()()
    try:
        removed = cleanup_expired(session)
        session.commit()
        return {"jobs": removed}
    finally:
        session.close()


async def retention_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(cleanup_once)
        except Exception as error:  # noqa: BLE001
            print(f"[docparse-retention] cleanup failed: {error}")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)

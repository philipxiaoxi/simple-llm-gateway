"""资讯媒体文件的保留期清理。

`INFO_RETENTION_DAYS` 默认 0 = 永久保留；设为正数时清理超期的媒体文件，置
`purged=1` 并保留条目与媒体元数据（前端据此回退到纯文字封面）。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.clock import utcnow
from app.config import get_settings
from app.db import get_session_factory
from app.info import storage
from app.models import InfoItem, InfoMedia

CLEANUP_INTERVAL_SECONDS = 24 * 3600


def cleanup_once() -> dict[str, int]:
    settings = get_settings()
    days = int(settings.info_retention_days or 0)
    session = get_session_factory()()
    purged = 0
    try:
        if days > 0:
            cutoff = utcnow() - timedelta(days=days)
            rows = list(
                session.scalars(
                    select(InfoMedia).where(
                        InfoMedia.purged == 0,
                        InfoMedia.status == "ready",
                        InfoMedia.created_at < cutoff,
                    )
                ).all()
            )
            touched_items: set[str] = set()
            for row in rows:
                item = session.get(InfoItem, row.item_id)
                if item is None:
                    continue
                path = storage.media_path(item.id, row.filename) if row.filename else None
                if path is not None:
                    path.unlink(missing_ok=True)
                row.purged = 1
                touched_items.add(item.id)
                purged += 1
            session.flush()
            from app.info import collector

            for item_id in touched_items:
                item = session.get(InfoItem, item_id)
                if item is not None:
                    collector.refresh_item_media_state(session, item)

        temp_removed = storage.cleanup_temp()
        session.commit()
        return {"media": purged, "temp": temp_removed}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def retention_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(cleanup_once)
        except Exception as error:  # noqa: BLE001
            print(f"[info-retention] cleanup failed: {error}")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)

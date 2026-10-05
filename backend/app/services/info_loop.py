"""资讯收集的后台循环：定时采集与媒体转存 worker。

两者都跑在独立 asyncio 任务里（由 main.py 的 lifespan 挂载），各自的同步逻辑用
`asyncio.to_thread` 执行，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio

from app.config import get_settings
from app.db import get_session_factory
from app.info import collector, sources
from app.info import media as media_service
from app.info.errors import InfoError
from app.services import info_ai

MEDIA_WORKER_INTERVAL_SECONDS = 5


def collect_due_once() -> dict[str, int]:
    """采集所有到期的渠道。单个渠道失败不影响其它渠道。"""
    settings = get_settings()
    session = get_session_factory()()
    collected = 0
    created = 0
    failed = 0
    try:
        due = sources.due_sources(session)
        for source in due[: max(1, settings.info_max_concurrent_sources)]:
            try:
                result = collector.collect_source(session, source)
                session.commit()
                collected += 1
                created += int(result.get("created") or 0)
            except InfoError as error:
                session.commit()
                failed += 1
                print(f"[info] 采集失败 {source.identifier}: {error.error_type} {error.message}")
            except Exception as error:  # noqa: BLE001
                session.rollback()
                failed += 1
                print(f"[info] 采集异常 {source.identifier}: {error}")
    finally:
        session.close()
    return {"sources": collected, "created": created, "failed": failed}


def download_pending_once() -> dict[str, int]:
    """转存一批待下载媒体。

    下载在**事务之外**执行：先短读拿快照并立即释放事务，再逐项下载，最后每项用一次
    短事务写回。否则耗时下载会长时间占住 SQLite 写锁，把整个站点拖成数据库锁等待。
    """
    settings = get_settings()
    factory = get_session_factory()

    session = factory()
    try:
        snapshot = collector.pending_media_snapshot(
            session, limit=settings.info_max_concurrent_downloads
        )
        session.rollback()
    finally:
        session.close()

    processed = 0
    succeeded = 0
    for entry in snapshot:
        processed += 1
        fetched = None
        error: object = None
        try:
            fetched = media_service.fetch_media(
                entry.item_id,
                index_no=entry.index_no,
                remote_url=entry.remote_url,
                kind=entry.kind,
                max_bytes=settings.info_max_item_bytes,
            )
        except Exception as caught:  # noqa: BLE001 - 单项失败不影响其它项
            error = caught

        session = factory()
        try:
            if collector.apply_media_result(session, entry.media_id, fetched=fetched, error=error):
                succeeded += 1
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
            raise
        finally:
            session.close()

    return {"processed": processed, "succeeded": succeeded}


async def collect_loop() -> None:
    interval = max(15, int(get_settings().info_tick_seconds))
    while True:
        try:
            result = await asyncio.to_thread(collect_due_once)
            if result["sources"] or result["failed"]:
                print(
                    f"[info] 采集完成：渠道 {result['sources']}，新增 {result['created']}，失败 {result['failed']}"
                )
        except Exception as error:  # noqa: BLE001
            print(f"[info] 采集循环异常: {error}")
        await asyncio.sleep(interval)


async def media_worker_loop() -> None:
    while True:
        try:
            result = await asyncio.to_thread(download_pending_once)
            if result.get("processed"):
                print(f"[info] 媒体转存：处理 {result['processed']}，成功 {result.get('succeeded')}")
        except Exception as error:  # noqa: BLE001
            print(f"[info] 媒体转存异常: {error}")
        await asyncio.sleep(MEDIA_WORKER_INTERVAL_SECONDS)


async def ai_score_loop() -> None:
    interval = max(5, int(get_settings().info_ai_tick_seconds))
    while True:
        try:
            result = await info_ai.score_pending_once()
            if result.get("processed"):
                print(
                    f"[info] AI 判定：处理 {result['processed']}，成功 {result.get('scored')}，"
                    f"失败 {result.get('failed')}，跳过 {result.get('skipped')}"
                )
        except Exception as error:  # noqa: BLE001
            print(f"[info] AI 判定循环异常: {error}")
        await asyncio.sleep(interval)


def start_info_workers() -> list[asyncio.Task]:
    return [
        asyncio.create_task(collect_loop()),
        asyncio.create_task(media_worker_loop()),
        asyncio.create_task(ai_score_loop()),
    ]

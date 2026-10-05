"""资讯收集的后台循环：定时采集与媒体转存 worker。

两者都跑在独立 asyncio 任务里（由 main.py 的 lifespan 挂载），各自的同步逻辑用
`asyncio.to_thread` 执行，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from app.config import get_settings
from app.db import get_session_factory
from app.info import collector, sources
from app.info import media as media_service
from app.info.adapters import build_excerpt
from app.info.errors import InfoError
from app.info.sanitize import sanitize_wechat_html
from app.models import InfoItem, InfoMedia, InfoSource
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


def _pending_wechat_snapshot(factory, *, limit: int, max_attempts: int):
    """短读：列出待补全正文的微信条目，并取回带凭据的适配器。

    正文补全在事务外进行；适配器只持有 base_url/api_key 字符串，会话关闭后仍可用。
    """
    session = factory()
    try:
        rows = list(
            session.execute(
                select(InfoItem.id, InfoItem.permalink, InfoItem.content_attempts)
                .join(InfoSource, InfoItem.source_id == InfoSource.id)
                .where(
                    InfoSource.kind == "wechat",
                    InfoItem.content_status != "done",
                    InfoItem.content_attempts < max_attempts,
                )
                .order_by(InfoItem.created_at.asc())
                .limit(max(1, limit))
            ).all()
        )
        adapter = collector.adapter_for(session, "wechat")
        session.rollback()
        return rows, adapter
    finally:
        session.close()


def enrich_wechat_content_once() -> dict[str, int]:
    """把一批微信条目的正文与正文图片补全，成功后才交给 AI 判定。"""
    settings = get_settings()
    factory = get_session_factory()
    snapshot, adapter = _pending_wechat_snapshot(
        factory,
        limit=settings.info_wechat_detail_batch_size,
        max_attempts=settings.info_wechat_detail_max_attempts,
    )

    processed = 0
    done = 0
    failed = 0
    for item_id, permalink, _attempts in snapshot:
        processed += 1
        article = None
        error = False
        try:
            article = adapter.fetch_article(permalink)
        except Exception as caught:  # noqa: BLE001 - 单条失败不影响整批
            error = True
            print(f"[info] 微信全文补全失败 {str(permalink)[:80]}: {caught}")

        text = article.text if article is not None else ""
        media = article.media if article is not None else []
        html = sanitize_wechat_html(article.html) if article is not None else ""

        session = factory()
        try:
            item = session.get(InfoItem, item_id)
            if item is None:
                session.rollback()
                continue
            item.content_attempts = int(item.content_attempts or 0) + 1
            if error or not text.strip():
                item.content_status = "failed"
                failed += 1
            else:
                item.text = text
                item.excerpt = build_excerpt(text, 200)
                # 保留原排版的清洗后 HTML；过大则退回纯文本
                item.content_html = (
                    html if html and len(html) <= settings.info_wechat_html_max_chars else None
                )
                existing = {row.remote_url for row in item.media}
                next_index = max((row.index_no for row in item.media), default=-1) + 1
                added = 0
                for entry in media or []:
                    if entry.remote_url in existing:
                        continue
                    if added >= settings.info_wechat_max_body_images:
                        break
                    session.add(
                        InfoMedia(
                            id=str(uuid.uuid4()),
                            item_id=item.id,
                            index_no=next_index,
                            kind="image",
                            remote_url=entry.remote_url[:1024],
                            status="pending",
                        )
                    )
                    existing.add(entry.remote_url)
                    next_index += 1
                    added += 1
                collector.refresh_item_media_state(session, item)
                item.content_status = "done"
                done += 1
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
            raise
        finally:
            session.close()
    return {"processed": processed, "done": done, "failed": failed}


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


async def wechat_content_loop() -> None:
    interval = max(5, int(get_settings().info_wechat_detail_interval_seconds))
    while True:
        try:
            result = await asyncio.to_thread(enrich_wechat_content_once)
            if result.get("processed"):
                print(
                    f"[info] 微信全文补全：处理 {result['processed']}，完成 {result.get('done')}，"
                    f"失败 {result.get('failed')}"
                )
        except Exception as error:  # noqa: BLE001
            print(f"[info] 微信全文补全循环异常: {error}")
        await asyncio.sleep(interval)


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
        asyncio.create_task(wechat_content_loop()),
        asyncio.create_task(media_worker_loop()),
        asyncio.create_task(ai_score_loop()),
    ]

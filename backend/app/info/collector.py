"""采集编排：拉取 → 去重入库 → 建媒体行 → 推进游标。

约定：

- 一条内容必须有文本或媒体其一，否则在入库前丢弃。
- `(source_id, external_id)` 唯一，重复采集幂等。
- 增量游标 `cursor_after` **只在本轮成功后推进**，失败保留原值下轮重试。
- 媒体先以 `pending` 入库，由后台 worker 转存（见 `download_pending_media`）。
"""

from __future__ import annotations

import uuid
import zlib
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.info import media as media_service
from app.info import storage
from app.info.adapters import (
    DISPLAY_MEDIA_KINDS,
    FetchedPost,
    SourceAdapter,
    SourcePreview,
    build_adapter,
    build_excerpt,
    classify,
    parse_count,
    reactions_total,
)
from app.info.errors import InfoError
from app.info.sanitize import sanitize_feed_html
from app.models import InfoItem, InfoMedia, InfoSource
from app.services.tikhub_config import get_tikhub_credentials


def adapter_for(db: Session, kind: str) -> SourceAdapter:
    base_url, api_key = get_tikhub_credentials(db)
    return build_adapter(kind, base_url=base_url, api_key=api_key)


def cover_seed_for(source_id: str, external_id: str) -> int:
    """纯文字封面的稳定随机种子：同一内容永远得到同一张封面。"""
    return zlib.crc32(f"{source_id}:{external_id}".encode()) & 0x7FFFFFFF


def _apply_channel(source: InfoSource, channel: SourcePreview) -> None:
    if channel.title:
        source.title = channel.title[:256]
    if channel.username:
        source.username = channel.username[:128]
    if channel.description:
        source.description = channel.description
    if channel.avatar_url:
        source.avatar_url = channel.avatar_url[:1024]
    if channel.subscriber_count_text:
        source.subscriber_count_text = channel.subscriber_count_text[:16]


def _insert_item(
    db: Session,
    source: InfoSource,
    post: FetchedPost,
    *,
    content_html: str | None = None,
    content_status: str | None = None,
) -> bool:
    """写入一条内容及其媒体行；已存在或内容为空时返回 False。

    存在性检查与写入放在同一个 SAVEPOINT 里，并用唯一约束兜底：定时循环与
    「立即采集」可能同时采同一个渠道，check-then-insert 之间存在竞态窗口，
    撞上 `UNIQUE(source_id, external_id)` 时只跳过这一条，不能让整轮回滚。
    """
    settings = get_settings()
    # 硬约束：一条内容必须有文本或媒体其一（适配器也会过滤，这里是兜底）
    if not post.text.strip() and not post.media:
        return False

    try:
        with db.begin_nested():
            exists = db.scalar(
                select(InfoItem.id).where(
                    InfoItem.source_id == source.id, InfoItem.external_id == post.external_id
                )
            )
            if exists:
                return False

            media_items = list(post.media)[: max(0, settings.info_max_media_per_item)]
            # RSS 等一次性拿到正文的渠道：把 post.html 清洗后作为正文 HTML 入库
            if content_html is None and post.html:
                content_html = sanitize_feed_html(post.html) or None
            item = InfoItem(
                id=str(uuid.uuid4()),
                source_id=source.id,
                external_id=post.external_id[:64],
                kind=classify(media_items),
                text=post.text,
                excerpt=build_excerpt(post.text, 200),
                permalink=post.permalink[:512],
                author_name=post.author_name[:128],
                source_type=post.source_type[:16],
                published_at=post.published_at,
                views=None,
                views_text=post.views_text[:16] if post.views_text else None,
                reactions_total=reactions_total(post.reactions),
                reactions_json=_dump_json(post.reactions) if post.reactions else None,
                is_forwarded=post.is_forwarded,
                link_preview_json=_dump_json(post.link_preview) if post.link_preview else None,
                media_count=sum(
                    1 for entry in media_items if entry.kind in DISPLAY_MEDIA_KINDS
                ),
                cover_seed=cover_seed_for(source.id, post.external_id),
                status="media_pending" if media_items else "ready",
                # 微信走全文模式：先入库列表字段，正文由后台 worker 补全后再交给 AI。
                # 手动保存的单篇直接带正文 HTML 入库（content_status 显式传入）。
                content_status=(
                    content_status
                    if content_status is not None
                    else ("pending" if source.kind == "wechat" else "")
                ),
                content_html=content_html,
                collected_at=utcnow(),
                created_at=utcnow(),
            )
            if item.views_text:
                item.views = parse_count(item.views_text)

            db.add(item)
            db.flush()

            rows: list[InfoMedia] = []
            for index, entry in enumerate(media_items):
                row = InfoMedia(
                    id=str(uuid.uuid4()),
                    item_id=item.id,
                    index_no=index,
                    kind=entry.kind,
                    remote_url=entry.remote_url[:1024],
                    duration_ms=entry.duration_ms,
                    status="pending",
                )
                db.add(row)
                rows.append(row)
            db.flush()

            # 封面优先取第一张图片，其次视频封面
            cover = next((row for row in rows if row.kind == "image"), None)
            if cover is None:
                cover = next((row for row in rows if row.kind == "poster"), None)
            item.cover_media_id = cover.id if cover is not None else None
            db.flush()
        return True
    except IntegrityError:
        return False


def insert_article(
    db: Session, source: InfoSource, post: FetchedPost, *, content_html: str | None = None
) -> str | None:
    """手动保存单篇文章：直接带正文 HTML 入库（content_status=done）。

    返回新条目 id；已存在（同 source + external_id）时返回 None。
    """
    if not _insert_item(db, source, post, content_html=content_html, content_status="done"):
        return None
    return db.scalar(
        select(InfoItem.id).where(
            InfoItem.source_id == source.id,
            InfoItem.external_id == post.external_id[:64],
        )
    )


def _dump_json(value: object) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return ""


def _fetch_backfill(
    adapter: SourceAdapter, identifier: str, limit: int, *, max_pages: int = 4
) -> tuple[list[FetchedPost], SourcePreview | None]:
    """首次采集：用 `before` 游标持续向更老翻页，直到取满 limit 条或没有更老的消息。

    只发 `after` 是拿不到历史的——上游语义里 `after` 是「取更新」，`before` 才是
    「取更老」。回填必须在 `before` 方向走，游标推进（`cursor_after`）才顺理成章地
    留给后续的增量轮询。`before` 是上游不透明游标：Telegram 为整数 post_id，
    微信公众号为 base64 字符串。
    """
    collected: list[FetchedPost] = []
    seen: set[str] = set()
    channel: SourcePreview | None = None
    before: int | str | None = None

    for _ in range(max(1, max_pages)):
        remaining = max(1, limit - len(collected))
        page = adapter.fetch(identifier, after=None, before=before, limit=min(100, remaining))
        if channel is None:
            channel = page.channel
        for post in page.posts:
            if post.external_id in seen:
                continue
            seen.add(post.external_id)
            collected.append(post)
        if len(collected) >= limit:
            break
        if page.has_more_before is False or not page.before_cursor:
            break
        before = page.before_cursor

    return collected[:limit], channel


def collect_source(
    db: Session, source: InfoSource, *, adapter: SourceAdapter | None = None, backfill: bool = False
) -> dict[str, object]:
    """采集一个渠道的一轮。抛 InfoError 时游标不推进，失败信息写入渠道。"""
    settings = get_settings()
    adapter = adapter or adapter_for(db, source.kind)

    # 先把需要的字段取出来，然后结束事务再发网络请求：上游调用可能耗时数十秒，
    # 攥着事务不放会让其它写操作（登录、收藏等）撞上 SQLite 的 `database is locked`。
    source_id = source.id
    identifier = source.identifier

    # 首次采集（尚无成功记录）回填更多历史。用 last_success_at 判定而非游标，
    # 这样字符串游标渠道（微信公众号）成功后不会每轮重复回填。
    first_run = source.last_success_at is None
    limit = settings.info_backfill_limit if (backfill or first_run) else settings.info_poll_limit
    after = None if (backfill or first_run) else source.cursor_after
    db.rollback()

    try:
        if backfill or first_run:
            posts, channel = _fetch_backfill(adapter, identifier, limit)
        else:
            page = adapter.fetch(identifier, after=after, limit=limit)
            posts, channel = page.posts, page.channel
    except InfoError as error:
        current = db.get(InfoSource, source_id)
        if current is not None:
            current.consecutive_failures = int(current.consecutive_failures or 0) + 1
            current.last_error = f"{error.error_type}: {error.message}"[:1000]
            current.last_polled_at = utcnow()
            db.flush()
        raise

    # rollback 之后 ORM 对象已过期，这里重新取回当前实例继续写
    source = db.get(InfoSource, source_id)
    if source is None:
        raise InfoError("渠道已被删除", status_code=404, error_type="source_not_found")
    source.last_polled_at = utcnow()

    if channel is not None:
        _apply_channel(source, channel)

    created = 0
    skipped = 0
    highest = int(source.cursor_after or 0)
    for post in posts:
        try:
            post_id = int(post.external_id)
        except (TypeError, ValueError):
            post_id = None
        if post_id is not None and post_id > highest:
            highest = post_id
        if _insert_item(db, source, post):
            created += 1
        else:
            skipped += 1

    # 只有整轮成功才推进游标
    if highest and (source.cursor_after is None or highest > int(source.cursor_after)):
        source.cursor_after = highest
    source.last_success_at = utcnow()
    source.last_error = None
    source.consecutive_failures = 0
    source.item_count = int(
        db.scalar(
            select(func.count()).select_from(InfoItem).where(InfoItem.source_id == source.id)
        )
        or 0
    )
    source.updated_at = utcnow()
    db.flush()
    return {
        "source_id": source.id,
        "fetched": len(posts),
        "created": created,
        "skipped": skipped,
        "error": None,
    }


def collect_many(db: Session, source_list: list[InfoSource]) -> dict[str, object]:
    """串行采集一组渠道（用于「批量立即采集 / 一键全部采集」）。

    单独渠道失败不影响其它渠道；每个渠道采集后立即提交，避免下一轮 `collect_source`
    内部的事务回滚把上一轮结果一并丢弃。返回聚合结果与每渠道明细。
    """
    targets = [
        (source.id, source.title or source.identifier, source.last_success_at is None)
        for source in source_list
    ]
    results: list[dict[str, object]] = []
    succeeded = 0
    failed = 0
    created = 0
    fetched = 0
    skipped = 0
    for source_id, title, first_run in targets:
        source = db.get(InfoSource, source_id)
        if source is None:
            continue
        try:
            result = collect_source(db, source, backfill=first_run)
            db.commit()
            succeeded += 1
            created += int(result.get("created") or 0)
            fetched += int(result.get("fetched") or 0)
            skipped += int(result.get("skipped") or 0)
            results.append({"source_id": source_id, "title": title, "error": None})
        except InfoError as error:
            # 失败信息已写入渠道（last_error / 连续失败次数），提交以持久化
            db.commit()
            failed += 1
            results.append(
                {
                    "source_id": source_id,
                    "title": title,
                    "error": {"type": error.error_type, "message": error.message},
                }
            )
        except Exception as error:  # noqa: BLE001 - 单渠道异常不影响整批
            db.rollback()
            failed += 1
            results.append(
                {
                    "source_id": source_id,
                    "title": title,
                    "error": {"type": "exception", "message": str(error)},
                }
            )
    return {
        "total": len(targets),
        "succeeded": succeeded,
        "failed": failed,
        "created": created,
        "fetched": fetched,
        "skipped": skipped,
        "results": results,
    }


def refresh_item_media_state(db: Session, item: InfoItem) -> None:
    """按媒体项状态汇总条目状态与可展示媒体数，并把封面收敛到「已就绪」的那一项。"""
    rows = list(item.media)
    if not rows:
        item.status = "ready"
        item.cover_media_id = None
        item.media_count = 0
        db.flush()
        return

    ready = [row for row in rows if row.status == "ready" and not row.purged]
    pending = [row for row in rows if row.status == "pending"]

    if pending:
        item.status = "media_pending"
    elif len(ready) == len(rows):
        item.status = "ready"
    elif ready:
        item.status = "media_partial"
    else:
        # 全部失败或已被保留期清理：没有任何可展示媒体
        item.status = "failed"

    # 可展示媒体数按「已就绪的图片/视频」复算，poster 与失败项不计入，
    # 否则卡片上的 1/N 角标会把拿不到的项目也算进去。
    item.media_count = sum(1 for row in ready if row.kind in DISPLAY_MEDIA_KINDS)

    cover = next((row for row in ready if row.kind == "image"), None)
    if cover is None:
        cover = next((row for row in ready if row.kind == "poster"), None)
    if cover is None:
        cover = next((row for row in ready if row.kind == "video"), None)
    item.cover_media_id = cover.id if cover is not None else None
    db.flush()


@dataclass(frozen=True)
class PendingMedia:
    media_id: str
    item_id: str
    index_no: int
    remote_url: str
    kind: str


def _error_text(error: object) -> str:
    if isinstance(error, InfoError):
        return f"{error.error_type}: {error.message}"[:1000]
    return str(error)[:1000]


def pending_media_snapshot(db: Session, *, limit: int) -> list[PendingMedia]:
    """短读：列出待转存的媒体。

    调用方取到快照后必须**立即结束事务**——下载是耗时网络操作，事务里做网络请求会
    长时间占住 SQLite 的写锁，导致其它写操作（例如登录）报 `database is locked`。
    """
    rows = list(
        db.scalars(
            select(InfoMedia)
            .where(InfoMedia.status == "pending", InfoMedia.purged == 0)
            .order_by(InfoMedia.created_at)
            .limit(max(1, limit))
        ).all()
    )
    return [
        PendingMedia(
            media_id=row.id,
            item_id=row.item_id,
            index_no=row.index_no,
            remote_url=row.remote_url,
            kind=row.kind,
        )
        for row in rows
    ]


def apply_media_result(
    db: Session, media_id: str, *, fetched: media_service.FetchedFile | None = None, error: object = None
) -> bool:
    """短写：把转存结果写回媒体行并刷新条目状态。返回是否成功。"""
    row = db.get(InfoMedia, media_id)
    if row is None:
        return False
    item = db.get(InfoItem, row.item_id)

    if fetched is not None:
        row.filename = fetched.filename
        row.content_type = fetched.content_type
        row.size_bytes = fetched.size_bytes
        row.sha256 = fetched.sha256
        row.width = fetched.width
        row.height = fetched.height
        row.status = "ready"
        row.error_message = None
        succeeded = True
    else:
        row.status = "failed"
        row.error_message = _error_text(error) if error is not None else "转存失败"
        succeeded = False

    db.flush()
    if item is not None:
        refresh_item_media_state(db, item)
    return succeeded


def reconcile_stuck_media(db: Session) -> int:
    """启动时把上次中断遗留的 pending 媒体重新排队（清掉半成品）。

    媒体文件本身是临时名 + 改名写入，不存在半成品被当作成功的情况；这里只负责把
    `pending` 交回 worker，安静地重试一次。
    """
    storage.cleanup_temp()
    rows = list(db.scalars(select(InfoMedia).where(InfoMedia.status == "pending")).all())
    for row in rows:
        row.error_message = None
    db.flush()
    return len(rows)


def delete_source(db: Session, source: InfoSource, *, purge_items: bool = False) -> None:
    """删除渠道。

    `purge_items=False`（默认）保留已采集内容与媒体文件，只把内容的 `source_id` 置空；
    `purge_items=True` 连带删除内容、媒体行与磁盘文件。
    """
    item_ids = list(db.scalars(select(InfoItem.id).where(InfoItem.source_id == source.id)).all())

    if purge_items:
        for item_id in item_ids:
            item = db.get(InfoItem, item_id)
            if item is not None:
                db.delete(item)  # 媒体行随之级联删除
        db.flush()
    else:
        db.execute(
            update(InfoItem).where(InfoItem.source_id == source.id).values(source_id=None)
        )

    db.delete(source)
    db.flush()

    if purge_items:
        for item_id in item_ids:
            storage.purge_item(item_id)

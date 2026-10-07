"""资讯条目的查询、游标分页与序列化。

分页用游标而不是 offset：采集过程中新内容会不断插入，offset 分页会让已翻过的页
出现重复或漏项。排序键统一取 `coalesce(published_at, collected_at)`，保证没有发布
时间的条目也有稳定的排序位置。
"""

from __future__ import annotations

import base64
import html as html_lib
import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.info import tokens
from app.info.adapters import DISPLAY_MEDIA_KINDS
from app.info.content_style import adapt_dark_theme
from app.models import InfoItem, InfoMedia, InfoSource

MAX_LIMIT = 60
DEFAULT_LIMIT = 24


def _sort_key():
    return func.coalesce(InfoItem.published_at, InfoItem.collected_at)


def encode_cursor(sort_value: datetime, item_id: str) -> str:
    raw = f"{sort_value.isoformat()}|{item_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
        raw_ts, item_id = raw.split("|", 1)
        return datetime.fromisoformat(raw_ts), item_id
    except (ValueError, UnicodeDecodeError):
        return None


def _filters(
    *,
    source_id: str | None,
    kind: str | None,
    query: str | None,
    favorite: bool,
    include_hidden: bool,
    featured: bool = False,
    label: str | None = None,
    min_score: int | None = None,
    ai_status: str | None = None,
) -> list[Any]:
    clauses: list[Any] = []
    if source_id:
        clauses.append(InfoItem.source_id == source_id)
    if kind:
        # 支持逗号分隔的多类型（例如「图文」= image,mixed）
        values = [value.strip() for value in kind.split(",") if value.strip()]
        if len(values) == 1:
            clauses.append(InfoItem.kind == values[0])
        elif values:
            clauses.append(InfoItem.kind.in_(values))
    if featured:
        clauses.append(InfoItem.is_featured.is_(True))
    if label:
        clauses.append(InfoItem.ai_label == label)
    if min_score is not None:
        clauses.append(InfoItem.ai_score.is_not(None))
        clauses.append(InfoItem.ai_score >= int(min_score))
    if ai_status:
        # 支持逗号分隔的多状态（例如「进行中」= pending,processing）
        values = [value.strip() for value in ai_status.split(",") if value.strip()]
        if len(values) == 1:
            clauses.append(InfoItem.ai_status == values[0])
        elif values:
            clauses.append(InfoItem.ai_status.in_(values))
    if query:
        # 转义 LIKE 通配符：否则用户输入 `%` 会命中整表、`_` 会变成任意单字符
        escaped = (
            query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        clauses.append(
            or_(
                InfoItem.text.ilike(pattern, escape="\\"),
                InfoItem.excerpt.ilike(pattern, escape="\\"),
            )
        )
    if favorite:
        clauses.append(InfoItem.is_favorite.is_(True))
    if not include_hidden:
        clauses.append(InfoItem.is_hidden.is_(False))
    return clauses


def list_items(
    db: Session,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
    source_id: str | None = None,
    kind: str | None = None,
    query: str | None = None,
    favorite: bool = False,
    include_hidden: bool = False,
    featured: bool = False,
    label: str | None = None,
    min_score: int | None = None,
    ai_status: str | None = None,
    order: str = "desc",
    sort: str = "timeline",
) -> tuple[list[InfoItem], str | None, int]:
    size = max(1, min(MAX_LIMIT, int(limit or DEFAULT_LIMIT)))
    ascending = (order or "desc").strip().lower() == "asc"
    clauses = _filters(
        source_id=source_id,
        kind=kind,
        query=query,
        favorite=favorite,
        include_hidden=include_hidden,
        featured=featured,
        label=label,
        min_score=min_score,
        ai_status=ai_status,
    )

    total = int(
        db.scalar(select(func.count()).select_from(InfoItem).where(*clauses)) or 0
    )

    # 「最近完成」排序：按判定时间倒序，未判定的排最后。判定进度弹窗用，不做游标翻页。
    if (sort or "").strip().lower() == "scored":
        stmt = (
            select(InfoItem)
            .options(selectinload(InfoItem.media), selectinload(InfoItem.source))
            .where(*clauses)
            .order_by(
                InfoItem.ai_scored_at.is_(None).asc(),
                InfoItem.ai_scored_at.desc(),
                InfoItem.id.desc(),
            )
            .limit(size)
        )
        return list(db.scalars(stmt).all()), None, total

    sort_key = _sort_key()
    # 列表要渲染封面（media）与频道信息（source），预加载避免每条各查一次（N+1）
    stmt = select(InfoItem).options(selectinload(InfoItem.media), selectinload(InfoItem.source)).where(*clauses)
    decoded = decode_cursor(cursor) if cursor else None
    if decoded is not None:
        ts, last_id = decoded
        # 翻页比较方向必须与排序方向一致，否则会出现重复或漏项
        if ascending:
            stmt = stmt.where(or_(sort_key > ts, and_(sort_key == ts, InfoItem.id > last_id)))
        else:
            stmt = stmt.where(or_(sort_key < ts, and_(sort_key == ts, InfoItem.id < last_id)))

    # 多取一条判断是否还有下一页
    if ascending:
        stmt = stmt.order_by(sort_key.asc(), InfoItem.id.asc()).limit(size + 1)
    else:
        stmt = stmt.order_by(sort_key.desc(), InfoItem.id.desc()).limit(size + 1)
    rows = list(db.scalars(stmt).all())
    has_more = len(rows) > size
    page = rows[:size]

    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor(last.published_at or last.collected_at, last.id)
    return page, next_cursor, total


def get_item(db: Session, item_id: str) -> InfoItem | None:
    return db.get(
        InfoItem, item_id, options=[selectinload(InfoItem.media), selectinload(InfoItem.source)]
    )


def _media_url(media: InfoMedia) -> str:
    return tokens.media_path(media.id, tokens.make_token(media.id))


def _serialize_cover(item: InfoItem) -> dict[str, Any] | None:
    if not item.cover_media_id:
        return None
    row = next((entry for entry in item.media if entry.id == item.cover_media_id), None)
    if row is None or row.status != "ready" or row.purged:
        return None
    payload = {
        "media_id": row.id,
        "kind": "video" if row.kind == "video" else "image",
        "url": _media_url(row),
        "width": row.width,
        "height": row.height,
        "duration_ms": row.duration_ms,
    }
    # 视频条目的封面通常是 poster，列表里再给一个可播放地址，供桌面端 hover 预览；
    # 浏览器只会在真正挂载 <video> 时才去取字节，不会预加载。
    video = next(
        (entry for entry in item.media if entry.kind == "video" and entry.status == "ready" and not entry.purged),
        None,
    )
    if video is not None:
        payload["video_url"] = _media_url(video)
    return payload


def _serialize_source(source: InfoSource | None) -> dict[str, Any] | None:
    if source is None:
        return None
    return {
        "id": source.id,
        "title": source.title,
        "username": source.username,
        "kind": source.kind,
        "avatar_url": source.avatar_url,
    }


def _parse_json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def serialize_item(item: InfoItem, *, include_media: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": item.id,
        "source": _serialize_source(item.source),
        "kind": item.kind,
        "text": item.text,
        "excerpt": item.excerpt,
        "permalink": item.permalink,
        "author_name": item.author_name,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "views": item.views,
        "views_text": item.views_text,
        "reactions_total": item.reactions_total,
        "reactions": _parse_json(item.reactions_json) or [],
        "media_count": int(item.media_count or 0),
        "cover_seed": int(item.cover_seed or 0),
        "cover": _serialize_cover(item),
        "status": item.status,
        "is_favorite": bool(item.is_favorite),
        "is_hidden": bool(item.is_hidden),
        "is_forwarded": bool(item.is_forwarded),
        "link_preview": _parse_json(item.link_preview_json),
        "ai_status": item.ai_status,
        "ai_label": item.ai_label,
        "ai_score": item.ai_score,
        "ai_reason": item.ai_reason,
        "ai_tags": _parse_json(item.ai_tags_json) or [],
        "ai_model": item.ai_model,
        "ai_error": item.ai_error,
        "ai_scored_at": item.ai_scored_at.isoformat() if item.ai_scored_at else None,
        "is_featured": bool(item.is_featured),
        "collected_at": item.collected_at.isoformat() if item.collected_at else None,
    }
    if include_media:
        payload["media"] = _serialize_media(item)
        # 公众号正文 HTML 可能较大，只在详情接口下发；
        # 图片改写成本地媒体地址，并把内联样式适配到深色主题
        if item.content_html:
            payload["content_html"] = adapt_dark_theme(
                _rewrite_content_images(item.content_html, item)
            )
        else:
            payload["content_html"] = None
    return payload


_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC_ATTR = re.compile(r'(?:data-src|src)\s*=\s*"([^"]*)"', re.IGNORECASE)


def _rewrite_content_images(html: str, item: InfoItem) -> str:
    """把正文 HTML 里的公众号图片改写成平台本地媒体地址。

    上游图片有防盗链，浏览器直连会 403；这里按 remote_url 匹配已转存好的媒体行，
    把 `data-src`/`src` 换成带令牌的本地地址。未转存成功的图片保持原样。
    """
    def normalize(url: str) -> str:
        # 兼容历史数据：remote_url 可能是 `&amp;` 转义形态，正文里是 `&`
        return html_lib.unescape(url or "").split("#")[0]

    mapping: dict[str, str] = {}
    for row in item.media:
        if row.kind != "image" or row.status != "ready" or row.purged:
            continue
        key = normalize(row.remote_url)
        if key:
            mapping[key] = _media_url(row)
    if not mapping:
        return html

    def replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        local: str | None = None
        for found in _SRC_ATTR.finditer(tag):
            url = normalize(found.group(1).strip())
            if url in mapping:
                local = mapping[url]
                break
        if local is None:
            return tag
        stripped = _SRC_ATTR.sub("", tag)
        if stripped.endswith("/>"):
            return stripped[:-2] + f' src="{local}"/>'
        return stripped[:-1] + f' src="{local}">'

    return _IMG_TAG.sub(replace, html)


def _serialize_media(item: InfoItem) -> list[dict[str, Any]]:
    rows = sorted(item.media, key=lambda entry: entry.index_no)
    by_index = {row.index_no: row for row in rows}

    entries: list[dict[str, Any]] = []
    for row in rows:
        if row.kind not in DISPLAY_MEDIA_KINDS:
            continue
        entry: dict[str, Any] = {
            "id": row.id,
            "kind": row.kind,
            "index_no": row.index_no,
            "url": _media_url(row) if row.status == "ready" and not row.purged else None,
            "width": row.width,
            "height": row.height,
            "duration_ms": row.duration_ms,
            "status": row.status,
            "error_message": row.error_message,
        }
        if row.kind == "video":
            # 视频封面紧邻其视频之前存放，按序号配对
            poster = by_index.get(row.index_no - 1)
            if poster is not None and poster.kind == "poster" and poster.status == "ready" and not poster.purged:
                entry["poster_url"] = _media_url(poster)
        entries.append(entry)
    return entries


def stats(db: Session) -> dict[str, Any]:
    from app.services.tikhub_config import tikhub_status

    source_count = int(db.scalar(select(func.count()).select_from(InfoSource)) or 0)
    item_count = int(db.scalar(select(func.count()).select_from(InfoItem)) or 0)
    media_bytes = int(
        db.scalar(
            select(func.coalesce(func.sum(InfoMedia.size_bytes), 0)).where(InfoMedia.purged == 0)
        )
        or 0
    )
    last_collect = db.scalar(select(func.max(InfoSource.last_polled_at)))
    provider = tikhub_status(db)
    # 一次分组查询取各判定状态数量，避免为每个状态各查一次
    status_rows = db.execute(
        select(InfoItem.ai_status, func.count()).group_by(InfoItem.ai_status)
    ).all()
    ai_counts = {str(status or "pending"): int(count) for status, count in status_rows}
    ai_pending = ai_counts.get("pending", 0)
    ai_processing = ai_counts.get("processing", 0)
    ai_failed = ai_counts.get("failed", 0)
    ai_done = ai_counts.get("done", 0)
    ai_skipped = ai_counts.get("skipped", 0)
    featured_count = int(
        db.scalar(select(func.count()).select_from(InfoItem).where(InfoItem.is_featured.is_(True)))
        or 0
    )
    return {
        "source_count": source_count,
        "item_count": item_count,
        "media_bytes": media_bytes,
        "last_collect_at": last_collect.isoformat() if last_collect else None,
        "provider_configured": provider["configured"],
        "ai_pending": ai_pending + ai_processing,
        "ai_queued": ai_pending,
        "ai_processing": ai_processing,
        "ai_done": ai_done,
        "ai_failed": ai_failed,
        "ai_skipped": ai_skipped,
        "featured_count": featured_count,
    }

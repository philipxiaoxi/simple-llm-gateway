"""资讯上报：Agent 经 MCP / REST 上送内容，落进内置「其他」渠道，入库即精选公开。

与自动采集（`collector`）共用 `InfoSource` / `InfoItem` / `InfoMedia`，但媒体走字节直存
（`media.store_uploaded_media`），全程不做上游网络请求。本模块只依赖 `app.info.*`，
能力平面通过 `capabilities/info/provider.py` 调用这里，反向不依赖能力平面。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.info import collector, sources, storage
from app.info import media as media_service
from app.info.adapters import build_excerpt, parse_datetime
from app.info.errors import InfoError
from app.models import InfoItem, InfoMedia, McpKey
from app.services.info_report_gate import InfoReportRateLimited, info_report_gate

MAX_EXCERPT = 200
DISPLAY_KINDS = ("image", "video")


@dataclass(frozen=True)
class MediaFile:
    filename: str
    content_type: str
    data: bytes
    kind: str = ""
    duration_ms: int | None = None


@dataclass(frozen=True)
class ReportInput:
    text: str = ""
    title: str = ""
    url: str = ""
    author: str = ""
    published_at: datetime | None = None
    external_id: str = ""
    media: list[MediaFile] = field(default_factory=list)


def parse_published_at(value: object) -> datetime | None:
    """解析上报的 ISO8601 时间；缺失或非法返回 None（调用方回落采集时间）。"""
    parsed = parse_datetime(value)
    return parsed


def build_input(fields: dict[str, Any], media: list[MediaFile] | None = None) -> ReportInput:
    """从 REST 表单字段或 MCP JSON 参数构造 ReportInput。"""
    return ReportInput(
        text=str(fields.get("text") or ""),
        title=str(fields.get("title") or ""),
        url=str(fields.get("url") or ""),
        author=str(fields.get("author") or ""),
        published_at=parse_published_at(fields.get("published_at")),
        external_id=str(fields.get("external_id") or ""),
        media=list(media or []),
    )


def parse_json_media(raw: object, *, max_bytes: int) -> list[MediaFile]:
    """解析 JSON（MCP / REST JSON）里的 base64 媒体项。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise InfoError("media 必须为数组", status_code=400, error_type="invalid_request")
    out: list[MediaFile] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise InfoError("media 项必须为对象", status_code=400, error_type="invalid_request")
        encoded = str(entry.get("content_base64") or entry.get("content") or "")
        if not encoded:
            raise InfoError("media 项缺少 content_base64", status_code=400, error_type="invalid_request")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise InfoError(
                "content_base64 不是合法 base64", status_code=400, error_type="invalid_request"
            ) from None
        if len(data) > max_bytes:
            raise InfoError(
                f"单个媒体超过 {max_bytes} 字节，请改用 REST multipart",
                status_code=413,
                error_type="too_large",
            )
        duration = entry.get("duration_ms")
        out.append(
            MediaFile(
                filename=str(entry.get("filename") or ""),
                content_type=str(entry.get("content_type") or ""),
                data=data,
                kind=str(entry.get("kind") or ""),
                duration_ms=int(duration) if isinstance(duration, int | float) else None,
            )
        )
    return out


def limits() -> dict[str, Any]:
    settings = get_settings()
    return {
        "enabled": bool(settings.info_report_enabled),
        "max_text_chars": settings.info_report_max_text_chars,
        "max_title_chars": settings.info_report_max_title_chars,
        "max_file_bytes": settings.info_report_max_file_bytes,
        "max_mcp_file_bytes": settings.info_report_max_mcp_file_bytes,
        "max_files_per_item": settings.info_report_max_files_per_item,
        "max_item_bytes": settings.info_report_max_item_bytes,
        "batch_max_items": settings.info_report_batch_max_items,
        "rate_per_minute": settings.info_report_rate_per_minute,
    }


def _clean_text(value: str, limit: int) -> str:
    text = (value or "").replace("\u200b", "").replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")
    if len(text) > limit:
        raise InfoError(f"正文超过 {limit} 字上限", status_code=400, error_type="invalid_request")
    return text


def _clean_title(value: str, limit: int) -> str:
    return (value or "").strip()[:limit]


def _dedupe_key(item: ReportInput, text: str) -> str:
    if item.external_id.strip():
        return item.external_id.strip()[:64]
    parts = [item.url.strip(), text]
    for entry in item.media:
        parts.append(hashlib.sha256(entry.data).hexdigest())
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]


def _classify(kinds: list[str]) -> str:
    has_video = "video" in kinds
    has_image = "image" in kinds
    if has_video and has_image:
        return "mixed"
    if has_video:
        return "video"
    if has_image:
        return "image"
    return "text"


def _serialize(record: InfoItem, source_id: str, *, duplicate: bool) -> dict[str, Any]:
    return {
        "id": record.id,
        "source_id": source_id,
        "status": record.status,
        "duplicate": duplicate,
        "media_count": int(record.media_count or 0),
        "is_featured": bool(record.is_featured),
    }


def _check_rate(mcp_key: McpKey) -> None:
    try:
        info_report_gate.check(f"key:{mcp_key.id}")
    except InfoReportRateLimited:
        raise InfoError("上报过于频繁，请稍后重试", status_code=429, error_type="rate_limited") from None


def save_report(db: Session, *, mcp_key: McpKey, item: ReportInput) -> dict[str, Any]:
    """写入一条上报。返回 id / 状态 / 去重标记。"""
    settings = get_settings()
    if not settings.info_report_enabled:
        raise InfoError("资讯上报功能未开放", status_code=503, error_type="service_disabled")
    _check_rate(mcp_key)

    text = _clean_text(item.text, settings.info_report_max_text_chars)
    title = _clean_title(item.title, settings.info_report_max_title_chars)
    # InfoItem 无独立 title 列：标题折进正文首行，瀑布流摘要随之带上
    if title:
        text = f"{title}\n\n{text}".strip() if text else title
    if not text.strip() and not item.media:
        raise InfoError("请提供正文或媒体", status_code=400, error_type="invalid_request")
    if len(item.media) > settings.info_report_max_files_per_item:
        raise InfoError("单条媒体数超过上限", status_code=400, error_type="invalid_request")
    total_bytes = 0
    for entry in item.media:
        if len(entry.data) > settings.info_report_max_file_bytes:
            raise InfoError("单个媒体文件超过上限", status_code=413, error_type="too_large")
        total_bytes += len(entry.data)
    if total_bytes > settings.info_report_max_item_bytes:
        raise InfoError("单条媒体总大小超过上限", status_code=413, error_type="too_large")

    external_id = _dedupe_key(item, text)
    author = (item.author.strip() or mcp_key.name or "").strip()[:128]
    source = sources.ensure_manual_source(db)

    existing = db.scalar(
        select(InfoItem).where(InfoItem.source_id == source.id, InfoItem.external_id == external_id)
    )
    if existing is not None:
        return _serialize(existing, source.id, duplicate=True)

    item_id = str(uuid.uuid4())
    try:
        fetched = [
            media_service.store_uploaded_media(
                item_id,
                index_no=index,
                content_type=entry.content_type,
                data=entry.data,
                kind=entry.kind,
            )
            for index, entry in enumerate(item.media)
        ]
    except Exception:
        storage.purge_item(item_id)
        raise

    kinds = ["video" if row.content_type.startswith("video/") else "image" for row in fetched]
    media_rows = [
        InfoMedia(
            id=str(uuid.uuid4()),
            item_id=item_id,
            index_no=index,
            kind=kind,
            remote_url="",
            filename=row.filename,
            content_type=row.content_type,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            width=row.width,
            height=row.height,
            duration_ms=item.media[index].duration_ms,
            status="ready",
        )
        for index, (row, kind) in enumerate(zip(fetched, kinds, strict=True))
    ]
    cover = next((row for row in media_rows if row.kind == "image"), None)
    if cover is None:
        cover = next((row for row in media_rows if row.kind == "video"), None)

    record = InfoItem(
        id=item_id,
        source_id=source.id,
        external_id=external_id,
        kind=_classify(kinds),
        text=text,
        excerpt=build_excerpt(text, MAX_EXCERPT),
        permalink=(item.url or "")[:512],
        author_name=author,
        source_type="report",
        published_at=item.published_at or utcnow(),
        media_count=sum(1 for kind in kinds if kind in DISPLAY_KINDS),
        cover_media_id=cover.id if cover is not None else None,
        cover_seed=collector.cover_seed_for(source.id, external_id),
        status="ready",
        # 可见性走常规逻辑：默认未精选，交由 AI 判定决定精选与隐藏，不写人工覆盖标记
        is_featured=False,
        is_hidden=False,
        ai_status="pending",
        collected_at=utcnow(),
        created_at=utcnow(),
    )

    try:
        with db.begin_nested():
            db.add(record)
            for row in media_rows:
                db.add(row)
            db.flush()
    except IntegrityError:
        # 并发下另一请求已写入同去重键：清理刚落盘的媒体并返回已存在条目
        storage.purge_item(item_id)
        existing = db.scalar(
            select(InfoItem).where(InfoItem.source_id == source.id, InfoItem.external_id == external_id)
        )
        if existing is not None:
            return _serialize(existing, source.id, duplicate=True)
        raise InfoError("写入冲突", status_code=409, error_type="conflict") from None
    except Exception:
        # 任何写入失败都清理刚落盘的媒体，避免留下孤儿文件
        storage.purge_item(item_id)
        raise

    source.item_count = int(
        db.scalar(select(func.count()).select_from(InfoItem).where(InfoItem.source_id == source.id)) or 0
    )
    source.updated_at = utcnow()
    db.flush()
    return _serialize(record, source.id, duplicate=False)


def save_reports(db: Session, *, mcp_key: McpKey, items: list[ReportInput]) -> dict[str, Any]:
    """批量上报：逐条独立处理，单条失败不影响其余。"""
    settings = get_settings()
    if not settings.info_report_enabled:
        raise InfoError("资讯上报功能未开放", status_code=503, error_type="service_disabled")
    if len(items) > settings.info_report_batch_max_items:
        raise InfoError(
            f"单次批量不超过 {settings.info_report_batch_max_items} 条",
            status_code=400,
            error_type="invalid_request",
        )

    results: list[dict[str, Any]] = []
    created = duplicates = failed = 0
    for index, entry in enumerate(items):
        try:
            with db.begin_nested():
                outcome = save_report(db, mcp_key=mcp_key, item=entry)
        except InfoError as error:
            failed += 1
            results.append(
                {"index": index, "error": {"type": error.error_type, "message": error.message}}
            )
            continue
        except Exception as error:  # noqa: BLE001 - 单条异常不影响整批
            failed += 1
            results.append(
                {"index": index, "error": {"type": "internal_error", "message": str(error)}}
            )
            continue
        if outcome["duplicate"]:
            duplicates += 1
        else:
            created += 1
        results.append({"index": index, **outcome})

    return {"created": created, "duplicates": duplicates, "failed": failed, "items": results}

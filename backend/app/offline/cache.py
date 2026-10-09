"""离线下载的服务器端缓存。

解析后在后台异步把文件缓存到服务器：`queued` → `caching` → `ready` / `failed`。
元数据存 `offline_downloads` 表，文件放在 data/offline 目录，超配额时按最久未访问淘汰。
只有 `ready` 的记录才提供下载。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.models import OfflineDownload
from app.offline.errors import OfflineError

_EXT_FALLBACK = ".bin"

ACTIVE_STATUSES = frozenset({"queued", "caching"})
TERMINAL_STATUSES = frozenset({"ready", "failed"})


def cache_dir() -> Path:
    path = get_settings().resolved_offline_cache_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def icons_dir() -> Path:
    path = cache_dir() / "icons"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ext(filename: str) -> str:
    suffix = Path(filename or "").suffix
    return suffix if suffix and len(suffix) <= 12 else _EXT_FALLBACK


def _icon_ext(icon_url: str, content_type: str = "") -> str:
    suffix = Path(urlparse(icon_url or "").path).suffix
    if suffix and len(suffix) <= 6:
        return suffix
    lowered = content_type.lower()
    if "png" in lowered:
        return ".png"
    if "svg" in lowered:
        return ".svg"
    if "webp" in lowered:
        return ".webp"
    if "jpeg" in lowered or "jpg" in lowered:
        return ".jpg"
    if "icon" in lowered:
        return ".ico"
    return ".png"


def _stored_name(key: str, filename: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return f"{digest}{_ext(filename)}"


def target_path(row: OfflineDownload) -> Path:
    return cache_dir() / row.stored_name


def status_of(row: OfflineDownload) -> str:
    return row.status or "ready"


def get_by_key(db: Session, key: str) -> OfflineDownload | None:
    return db.scalar(select(OfflineDownload).where(OfflineDownload.cache_key == key))


def get_cached(db: Session, key: str) -> OfflineDownload | None:
    """仅在记录已缓存完成且文件仍在时返回；进行中/失败返回 None。"""
    row = get_by_key(db, key)
    if row is None:
        return None
    if status_of(row) != "ready":
        return None
    if not row.stored_name or not target_path(row).is_file():
        # 元数据在但文件丢了：清掉，让调用方重新缓存
        db.delete(row)
        db.flush()
        return None
    return row


def touch(db: Session, row: OfflineDownload) -> None:
    row.hit_count = int(row.hit_count or 0) + 1
    row.last_accessed_at = utcnow()


def file_response(row: OfflineDownload) -> FileResponse:
    return FileResponse(target_path(row), media_type=row.content_type or "application/octet-stream", filename=row.filename or None)


def enqueue(
    db: Session,
    *,
    provider: str,
    key: str,
    title: str = "",
    subtitle: str = "",
    description: str = "",
    icon_url: str = "",
    filename: str = "",
    content_type: str = "application/octet-stream",
    source: str = "",
    expected_bytes: int = 0,
    request: dict | None = None,
) -> OfflineDownload:
    """创建或复用一条缓存任务。

    已就绪或进行中的记录原样返回；失败的记录重置为排队，实现「重新缓存」。
    """
    payload = json.dumps(request or {}, ensure_ascii=False)
    row = get_by_key(db, key)
    if row is not None:
        status = status_of(row)
        if status in ACTIVE_STATUSES or status == "ready":
            return row
        row.status = "queued"
        row.stage = "queued"
        row.percent = 0
        row.message = "排队中"
        row.error_message = None
        row.bytes_downloaded = 0
        row.expected_bytes = int(expected_bytes or 0)
        row.request_json = payload
        row.updated_at = utcnow()
        db.flush()
        return row
    row = OfflineDownload(
        provider=provider,
        cache_key=key,
        title=(title or filename)[:255],
        subtitle=(subtitle or "")[:255],
        description=description or "",
        icon_url=(icon_url or "")[:512],
        icon_file="",
        filename=(filename or "")[:255],
        content_type=content_type or "application/octet-stream",
        stored_name="",
        source=(source or "")[:1024],
        size_bytes=0,
        hit_count=0,
        status="queued",
        stage="queued",
        percent=0,
        message="排队中",
        error_message=None,
        bytes_downloaded=0,
        expected_bytes=int(expected_bytes or 0),
        attempts=0,
        request_json=payload,
        created_at=utcnow(),
        updated_at=utcnow(),
        last_accessed_at=None,
    )
    db.add(row)
    db.flush()
    return row


def requeue(db: Session, row: OfflineDownload, *, request: dict | None = None, expected_bytes: int | None = None) -> OfflineDownload:
    """把记录重置为排队（用于「更新缓存」），保留旧文件直到新的下载完成覆盖。"""
    row.status = "queued"
    row.stage = "queued"
    row.percent = 0
    row.message = "排队中"
    row.error_message = None
    row.bytes_downloaded = 0
    if expected_bytes is not None:
        row.expected_bytes = int(expected_bytes)
    if request is not None:
        row.request_json = json.dumps(request, ensure_ascii=False)
    row.updated_at = utcnow()
    db.flush()
    return row


def update_progress(
    db: Session,
    row: OfflineDownload,
    *,
    stage: str | None = None,
    percent: int | None = None,
    message: str | None = None,
    downloaded: int | None = None,
    expected: int | None = None,
) -> None:
    if stage is not None:
        row.stage = stage[:16]
    if percent is not None:
        row.percent = max(0, min(100, int(percent)))
    if message is not None:
        row.message = message[:256]
    if downloaded is not None:
        row.bytes_downloaded = int(downloaded)
    if expected is not None:
        row.expected_bytes = int(expected)
    row.updated_at = utcnow()
    db.flush()


def finalize_file(
    db: Session,
    row: OfflineDownload,
    *,
    src_path: str,
    filename: str,
    content_type: str,
    icon_bytes: bytes | None = None,
    icon_url: str = "",
) -> OfflineDownload:
    """把已下载到临时路径的文件移动到缓存目录，并置为 ready。"""
    row.stored_name = _stored_name(row.cache_key, filename or row.filename)
    if filename:
        row.filename = filename[:255]
    if content_type:
        row.content_type = content_type
    dest = target_path(row)
    try:
        os.replace(src_path, dest)
    except OSError:
        shutil.copyfile(src_path, dest)
        with contextlib.suppress(OSError):
            os.unlink(src_path)
    size = dest.stat().st_size if dest.exists() else 0
    _mark_ready(db, row, size=size, icon_bytes=icon_bytes, icon_url=icon_url or row.icon_url)
    return row


def finalize_bytes(
    db: Session,
    row: OfflineDownload,
    *,
    data: bytes,
    filename: str,
    content_type: str,
    icon_bytes: bytes | None = None,
    icon_url: str = "",
) -> OfflineDownload:
    """把内存中的字节写入缓存目录，并置为 ready。"""
    row.stored_name = _stored_name(row.cache_key, filename or row.filename)
    if filename:
        row.filename = filename[:255]
    if content_type:
        row.content_type = content_type
    target_path(row).write_bytes(data)
    _mark_ready(db, row, size=len(data), icon_bytes=icon_bytes, icon_url=icon_url or row.icon_url)
    return row


def _mark_ready(
    db: Session,
    row: OfflineDownload,
    *,
    size: int,
    icon_bytes: bytes | None,
    icon_url: str,
) -> None:
    row.size_bytes = int(size)
    row.status = "ready"
    row.stage = "done"
    row.percent = 100
    row.message = "已缓存"
    row.error_message = None
    row.bytes_downloaded = int(size)
    row.updated_at = utcnow()
    row.last_accessed_at = utcnow()
    _write_icon(db, row, row.cache_key, icon_bytes, icon_url)
    db.flush()
    evict(db)


def mark_failed(db: Session, row: OfflineDownload, message: str) -> None:
    row.status = "failed"
    row.stage = "error"
    row.message = message[:256]
    row.error_message = message
    row.updated_at = utcnow()
    db.flush()


def mark_caching(db: Session, row: OfflineDownload, *, expected: int | None = None) -> None:
    row.status = "caching"
    row.stage = "download"
    row.percent = 1
    row.message = "开始缓存"
    row.error_message = None
    row.bytes_downloaded = 0
    if expected is not None:
        row.expected_bytes = int(expected)
    row.attempts = int(row.attempts or 0) + 1
    row.updated_at = utcnow()
    db.flush()


def _persist(db: Session, *, provider: str, key: str, title: str, subtitle: str, description: str, icon_url: str, filename: str, content_type: str, source: str, size: int) -> OfflineDownload:
    row = OfflineDownload(
        provider=provider,
        cache_key=key,
        title=title[:255],
        subtitle=subtitle[:255],
        description=description or "",
        icon_url=(icon_url or "")[:512],
        filename=filename[:255],
        content_type=content_type or "application/octet-stream",
        stored_name=_stored_name(key, filename),
        source=source[:1024],
        size_bytes=int(size),
        hit_count=0,
        status="ready",
        stage="done",
        percent=100,
        message="已缓存",
        error_message=None,
        bytes_downloaded=int(size),
        expected_bytes=int(size),
        attempts=0,
        request_json="{}",
        created_at=utcnow(),
        updated_at=utcnow(),
        last_accessed_at=utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _write_icon(db: Session, row: OfflineDownload, key: str, icon_bytes: bytes | None, icon_url: str) -> None:
    if not icon_bytes:
        return
    name = f"{hashlib.sha1((key + ':icon').encode('utf-8')).hexdigest()}{_icon_ext(icon_url)}"
    (icons_dir() / name).write_bytes(icon_bytes)
    row.icon_file = name


def store_bytes(
    db: Session,
    *,
    provider: str,
    key: str,
    data: bytes,
    filename: str,
    content_type: str,
    title: str = "",
    subtitle: str = "",
    description: str = "",
    icon_url: str = "",
    icon_bytes: bytes | None = None,
    source: str = "",
) -> OfflineDownload:
    row = _persist(
        db,
        provider=provider,
        key=key,
        title=title or filename,
        subtitle=subtitle,
        description=description,
        icon_url=icon_url,
        filename=filename,
        content_type=content_type,
        source=source,
        size=len(data),
    )
    target_path(row).write_bytes(data)
    _write_icon(db, row, key, icon_bytes, icon_url)
    evict(db)
    return row


def store_file(
    db: Session,
    *,
    provider: str,
    key: str,
    src_path: str,
    filename: str,
    content_type: str,
    title: str = "",
    subtitle: str = "",
    description: str = "",
    icon_url: str = "",
    icon_bytes: bytes | None = None,
    source: str = "",
) -> OfflineDownload:
    row = _persist(
        db,
        provider=provider,
        key=key,
        title=title or filename,
        subtitle=subtitle,
        description=description,
        icon_url=icon_url,
        filename=filename,
        content_type=content_type,
        source=source,
        size=os.path.getsize(src_path) if os.path.exists(src_path) else 0,
    )
    dest = target_path(row)
    try:
        os.replace(src_path, dest)
    except OSError:
        shutil.copyfile(src_path, dest)
        with contextlib.suppress(OSError):
            os.unlink(src_path)
    _write_icon(db, row, key, icon_bytes, icon_url)
    evict(db)
    return row


def list_cached(db: Session, *, provider: str | None = None, limit: int = 100) -> list[OfflineDownload]:
    size = max(1, min(500, int(limit or 100)))
    stmt = select(OfflineDownload)
    if provider:
        stmt = stmt.where(OfflineDownload.provider == provider)
    stmt = stmt.order_by(OfflineDownload.created_at.desc(), OfflineDownload.id.desc()).limit(size)
    return list(db.scalars(stmt).all())


def total_bytes(db: Session) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(OfflineDownload.size_bytes), 0)).where(
                OfflineDownload.status == "ready"
            )
        )
        or 0
    )


def evict(db: Session) -> None:
    limit = int(get_settings().offline_cache_max_bytes or 0)
    if limit <= 0:
        return
    while total_bytes(db) > limit:
        oldest = db.scalar(
            select(OfflineDownload)
            .where(OfflineDownload.status == "ready")
            .order_by(OfflineDownload.last_accessed_at.asc(), OfflineDownload.id.asc())
            .limit(1)
        )
        if oldest is None:
            break
        delete_cached(db, oldest.id, commit=False)


def delete_cached(db: Session, item_id: int, *, commit: bool = True) -> bool:
    row = db.get(OfflineDownload, item_id)
    if row is None:
        return False
    _remove_files(row)
    db.delete(row)
    if commit:
        db.commit()
    else:
        db.flush()
    return True


def delete_by_key(db: Session, key: str, *, commit: bool = False) -> bool:
    row = db.scalar(select(OfflineDownload).where(OfflineDownload.cache_key == key))
    if row is None:
        return False
    return delete_cached(db, row.id, commit=commit)


def _remove_files(row: OfflineDownload) -> None:
    if row.stored_name:
        path = target_path(row)
        if path.is_file():
            with contextlib.suppress(OSError):
                path.unlink()
    if row.icon_file:
        with contextlib.suppress(OSError):
            (icons_dir() / row.icon_file).unlink()


def serialize(row: OfflineDownload) -> dict[str, Any]:
    status = status_of(row)
    return {
        "id": row.id,
        "provider": row.provider,
        "title": row.title,
        "subtitle": row.subtitle,
        "description": row.description,
        "has_icon": bool(row.icon_file),
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": int(row.size_bytes or 0),
        "source": row.source,
        "hit_count": int(row.hit_count or 0),
        "status": status,
        "stage": row.stage or "done",
        "percent": int(row.percent or 0),
        "message": row.message or "",
        "error_message": row.error_message,
        "bytes_downloaded": int(row.bytes_downloaded or 0),
        "expected_bytes": int(row.expected_bytes or 0),
        "downloadable": status == "ready" and bool(row.stored_name),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "last_accessed_at": row.last_accessed_at.isoformat() if row.last_accessed_at else None,
    }


def require_cached(db: Session, item_id: int) -> OfflineDownload:
    row = db.get(OfflineDownload, item_id)
    if row is None or status_of(row) != "ready" or not row.stored_name or not target_path(row).is_file():
        raise OfflineError("缓存不存在或尚未完成", status_code=404)
    return row


def icon_response(row: OfflineDownload) -> FileResponse:
    if not row.icon_file:
        raise OfflineError("无图标", status_code=404)
    path = icons_dir() / row.icon_file
    if not path.is_file():
        raise OfflineError("图标文件不存在", status_code=404)
    media = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
    }.get(path.suffix.lower(), "image/png")
    return FileResponse(path, media_type=media, headers={"Cache-Control": "public, max-age=86400"})

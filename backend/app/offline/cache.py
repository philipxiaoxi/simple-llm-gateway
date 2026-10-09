"""离线下载的服务器端缓存。

下载一次后把文件留一份在本地磁盘，后续同名请求直接命中，不再回源。元数据存
`offline_downloads` 表，文件放在 data/offline 目录，超配额时按最久未访问淘汰。
"""

from __future__ import annotations

import contextlib
import hashlib
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


def get_cached(db: Session, key: str) -> OfflineDownload | None:
    row = db.scalar(select(OfflineDownload).where(OfflineDownload.cache_key == key))
    if row is None:
        return None
    if not row.stored_name or not target_path(row).is_file():
        # 元数据在但文件丢了：清掉，让调用方回源
        db.delete(row)
        db.flush()
        return None
    return row


def touch(db: Session, row: OfflineDownload) -> None:
    row.hit_count = int(row.hit_count or 0) + 1
    row.last_accessed_at = utcnow()


def file_response(row: OfflineDownload) -> FileResponse:
    return FileResponse(target_path(row), media_type=row.content_type or "application/octet-stream", filename=row.filename or None)


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
        created_at=utcnow(),
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
    return int(db.scalar(select(func.coalesce(func.sum(OfflineDownload.size_bytes), 0))) or 0)


def evict(db: Session) -> None:
    limit = int(get_settings().offline_cache_max_bytes or 0)
    if limit <= 0:
        return
    while total_bytes(db) > limit:
        oldest = db.scalar(select(OfflineDownload).order_by(OfflineDownload.last_accessed_at.asc(), OfflineDownload.id.asc()).limit(1))
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
    path = target_path(row)
    if path.is_file():
        with contextlib.suppress(OSError):
            path.unlink()
    if row.icon_file:
        with contextlib.suppress(OSError):
            (icons_dir() / row.icon_file).unlink()


def serialize(row: OfflineDownload) -> dict[str, Any]:
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
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_accessed_at": row.last_accessed_at.isoformat() if row.last_accessed_at else None,
    }


def require_cached(db: Session, item_id: int) -> OfflineDownload:
    row = db.get(OfflineDownload, item_id)
    if row is None or not row.stored_name or not target_path(row).is_file():
        raise OfflineError("缓存不存在或文件已删除", status_code=404)
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

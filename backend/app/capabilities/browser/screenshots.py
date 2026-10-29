from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.capabilities.browser import tokens
from app.capabilities.browser.errors import BrowserError
from app.clock import utcnow
from app.config import get_settings
from app.models import BrowserScreenshot, BrowserSession


def root() -> Path:
    return get_settings().resolved_browser_media_path


def session_dir(session_id: str) -> Path:
    path = root() / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def screenshot_path(session_id: str, filename: str) -> Path:
    return session_dir(session_id) / Path(filename).name


def screenshot_payload(row: BrowserScreenshot) -> dict:
    token = tokens.make_token(row.id)
    relative = f"/v1/browser/screenshots/{row.id}?token={token}"
    base = get_settings().app_base_url.rstrip("/")
    return {
        "screenshot_id": row.id,
        "screenshot_url": relative,
        "absolute_url": f"{base}{relative}",
        "content_type": row.content_type,
        "width": row.width,
        "height": row.height,
        "bytes": row.size_bytes,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


def save_screenshot(
    db: Session,
    session: BrowserSession,
    *,
    data: bytes,
    content_type: str,
    width: int,
    height: int,
) -> dict:
    settings = get_settings()
    if not data:
        raise BrowserError("截图内容为空", status_code=422, error_type="screenshot_failed")
    if len(data) > settings.browser_max_screenshot_bytes:
        raise BrowserError("截图超过大小上限", status_code=413, error_type="too_large")

    screenshot_id = str(uuid4())
    extension = "jpg" if "jpeg" in (content_type or "") else "png"
    filename = f"{screenshot_id}.{extension}"
    path = screenshot_path(session.id, filename)
    path.write_bytes(data)

    ttl = max(0, int(settings.browser_screenshot_ttl_seconds))
    expires = utcnow() + timedelta(seconds=ttl) if ttl else None
    row = BrowserScreenshot(
        id=screenshot_id,
        session_id=session.id,
        mcp_key_id=session.mcp_key_id,
        filename=filename,
        content_type=content_type or "image/png",
        width=max(0, int(width)),
        height=max(0, int(height)),
        size_bytes=len(data),
        expires_at=expires,
    )
    db.add(row)
    db.flush()
    return screenshot_payload(row)


def cleanup_expired(db: Session) -> int:
    """删除过期截图文件与记录。返回删除数量。"""
    settings = get_settings()
    ttl = max(0, int(settings.browser_screenshot_ttl_seconds))
    if not ttl:
        return 0
    cutoff = utcnow() - timedelta(seconds=ttl)
    stale = list(db.scalars(select(BrowserScreenshot).where(BrowserScreenshot.created_at < cutoff)).all())
    removed = 0
    for row in stale:
        path = screenshot_path(row.session_id, row.filename)
        path.unlink(missing_ok=True)
        db.delete(row)
        removed += 1
    if removed:
        db.flush()
    return removed


def latest_screenshot(db: Session, session_id: str) -> BrowserScreenshot | None:
    return db.scalar(
        select(BrowserScreenshot)
        .where(BrowserScreenshot.session_id == session_id)
        .order_by(BrowserScreenshot.created_at.desc())
        .limit(1)
    )


def purge_session_files(session_id: str) -> None:
    import shutil

    path = root() / session_id
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


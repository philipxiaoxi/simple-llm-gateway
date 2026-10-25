from __future__ import annotations

import contextlib
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.capabilities.browser import screenshots
from app.capabilities.browser.backends import BrowserBackend, PageSession
from app.capabilities.browser.errors import BrowserError
from app.clock import utcnow
from app.config import get_settings
from app.models import BrowserSession

_engine: BrowserBackend | None = None
_live: dict[str, PageSession] = {}


def configure_engine(engine: BrowserBackend | None) -> None:
    global _engine
    _engine = engine


def reset_for_tests() -> None:
    global _engine
    _engine = None
    _live.clear()


def get_engine() -> BrowserBackend:
    global _engine
    if _engine is None:
        from app.capabilities.browser.backends import LocalPlaywrightBackend

        _engine = LocalPlaywrightBackend()
    return _engine


def live_session(session_id: str) -> PageSession | None:
    return _live.get(session_id)


def session_payload(row: BrowserSession, *, screenshot: Any = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "mcp_key_id": row.mcp_key_id,
        "status": row.status,
        "current_url": row.current_url,
        "title": row.title,
        "viewport": {"width": row.viewport_width, "height": row.viewport_height},
        "backend": row.backend,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        "screenshot": screenshots.screenshot_payload(screenshot) if screenshot else None,
    }


def _active_total(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count()).select_from(BrowserSession).where(BrowserSession.status == "active")
        )
        or 0
    )


def _active_for_key(db: Session, mcp_key_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(BrowserSession)
            .where(BrowserSession.status == "active", BrowserSession.mcp_key_id == mcp_key_id)
        )
        or 0
    )


async def open_session(
    db: Session,
    *,
    mcp_key_id: int | None,
    url: str,
    width: int | None = None,
    height: int | None = None,
) -> tuple[BrowserSession, dict[str, Any]]:
    settings = get_settings()
    if _active_total(db) >= max(1, settings.browser_max_sessions):
        raise BrowserError("浏览器会话已达全局上限，请关闭闲置会话", status_code=429, error_type="too_many_sessions")
    if mcp_key_id is not None and _active_for_key(db, mcp_key_id) >= max(
        1, settings.browser_max_sessions_per_key
    ):
        raise BrowserError("该 Key 的浏览器会话已达上限", status_code=429, error_type="too_many_sessions")

    vw = max(320, int(width or settings.browser_viewport_width))
    vh = max(240, int(height or settings.browser_viewport_height))
    engine = get_engine()
    page = await engine.new_session(width=vw, height=vh, user_agent=settings.browser_user_agent)
    session = BrowserSession(
        id=str(uuid4()),
        mcp_key_id=mcp_key_id,
        status="active",
        viewport_width=vw,
        viewport_height=vh,
        backend=engine.name,
    )
    try:
        state = await page.goto(url)
    except Exception:
        await page.close()
        db.rollback()
        raise
    session.current_url = str(state.get("url") or "")[:2048]
    session.title = str(state.get("title") or "")[:512]
    session.last_used_at = utcnow()
    db.add(session)
    db.flush()
    _live[session.id] = page
    return session, state


def _owned(db: Session, session_id: str, mcp_key_id: int | None) -> BrowserSession:
    row = db.get(BrowserSession, session_id)
    if row is None:
        raise BrowserError("会话不存在", status_code=404, error_type="not_found")
    if mcp_key_id is not None and row.mcp_key_id != mcp_key_id:
        raise BrowserError("会话不存在", status_code=404, error_type="not_found")
    if row.status != "active":
        raise BrowserError("会话已关闭或过期", status_code=410, error_type="session_closed")
    return row


def require_page(db: Session, session_id: str, mcp_key_id: int | None) -> tuple[BrowserSession, PageSession]:
    row = _owned(db, session_id, mcp_key_id)
    page = _live.get(session_id)
    if page is None:
        row.status = "expired"
        row.closed_at = utcnow()
        db.flush()
        raise BrowserError("会话已过期，请重新打开", status_code=410, error_type="session_closed")
    row.last_used_at = utcnow()
    return row, page


def touch(db: Session, row: BrowserSession, state: dict[str, Any] | None = None) -> None:
    if state:
        if state.get("url"):
            row.current_url = str(state["url"])[:2048]
        if state.get("title") is not None:
            row.title = str(state["title"])[:512]
    row.last_used_at = utcnow()
    db.flush()


async def close_session(db: Session, session_id: str, mcp_key_id: int | None) -> None:
    row = db.get(BrowserSession, session_id)
    if row is None or (mcp_key_id is not None and row.mcp_key_id != mcp_key_id):
        raise BrowserError("会话不存在", status_code=404, error_type="not_found")
    page = _live.pop(session_id, None)
    if page is not None:
        with contextlib.suppress(Exception):
            await page.close()
    if row.status == "active":
        row.status = "closed"
        row.closed_at = utcnow()
        row.last_used_at = utcnow()
        db.flush()


def list_sessions(db: Session, *, mcp_key_id: int | None = None, limit: int = 100) -> list[BrowserSession]:
    query = select(BrowserSession).order_by(BrowserSession.created_at.desc()).limit(max(1, min(limit, 200)))
    if mcp_key_id is not None:
        query = query.where(BrowserSession.mcp_key_id == mcp_key_id)
    return list(db.scalars(query).all())


async def cleanup_idle(db: Session) -> int:
    """回收空闲会话与进程重启后残留的 active 会话。"""
    settings = get_settings()
    ttl = max(1, int(settings.browser_idle_ttl_seconds))
    cutoff = utcnow() - timedelta(seconds=ttl)
    rows = list(
        db.scalars(
            select(BrowserSession).where(
                BrowserSession.status == "active", BrowserSession.last_used_at < cutoff
            )
        ).all()
    )
    removed = 0
    for row in rows:
        page = _live.pop(row.id, None)
        if page is not None:
            with contextlib.suppress(Exception):
                await page.close()
        row.status = "expired"
        row.closed_at = utcnow()
        removed += 1
    if removed:
        db.flush()
    return removed

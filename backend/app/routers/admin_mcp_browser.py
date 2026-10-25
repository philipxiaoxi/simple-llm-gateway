from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.capabilities.browser import manager, screenshots
from app.capabilities.browser.errors import BrowserError
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_admin
from app.models import BrowserSession, McpKey

router = APIRouter(
    prefix="/api/admin/mcp/browser",
    tags=["admin-mcp-browser"],
    dependencies=[Depends(get_current_admin)],
)


def _http(error: BrowserError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"error": {"type": error.error_type, "message": error.message}},
    )


def _key_names(db: Session, rows: list[BrowserSession]) -> dict[int, str]:
    ids = {row.mcp_key_id for row in rows if row.mcp_key_id is not None}
    if not ids:
        return {}
    return {
        key.id: key.name
        for key in db.scalars(select(McpKey).where(McpKey.id.in_(ids))).all()
    }


def _payload(db: Session, row: BrowserSession, names: dict[int, str] | None = None) -> dict:
    body = manager.session_payload(
        row, screenshot=screenshots.latest_screenshot(db, row.id)
    )
    if names is not None and row.mcp_key_id is not None:
        body["mcp_key_name"] = names.get(row.mcp_key_id)
    return body


@router.get("/status")
def get_status(db: Session = Depends(get_db)):
    settings = get_settings()
    active = int(
        db.scalar(
            select(func.count()).select_from(BrowserSession).where(BrowserSession.status == "active")
        )
        or 0
    )
    return {
        "enabled": settings.browser_enabled,
        "backend": "remote" if settings.browser_worker_url else "local",
        "active_sessions": active,
        "max_sessions": settings.browser_max_sessions,
        "max_sessions_per_key": settings.browser_max_sessions_per_key,
        "idle_ttl_seconds": settings.browser_idle_ttl_seconds,
        "screenshot_ttl_seconds": settings.browser_screenshot_ttl_seconds,
        "allowed_hosts": settings.browser_allowed_hosts,
        "blocked_hosts": settings.browser_blocked_hosts,
    }


@router.get("/sessions")
def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    mcp_key_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    filters = []
    if status:
        filters.append(BrowserSession.status == status)
    if mcp_key_id is not None:
        filters.append(BrowserSession.mcp_key_id == mcp_key_id)
    total = db.scalar(select(func.count()).select_from(BrowserSession).where(*filters)) or 0
    rows = list(
        db.scalars(
            select(BrowserSession)
            .where(*filters)
            .order_by(BrowserSession.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    names = _key_names(db, rows)
    return {
        "items": [_payload(db, row, names) for row in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.get("/sessions/{session_id}")
def get_session(session_id: str, db: Session = Depends(get_db)):
    row = db.get(BrowserSession, session_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail={"error": {"type": "not_found", "message": "会话不存在"}}
        )
    names = _key_names(db, [row])
    return _payload(db, row, names)


@router.post("/sessions/{session_id}/screenshot")
async def capture_screenshot(
    session_id: str,
    payload: dict[str, Any] | None = None,
    db: Session = Depends(get_db),
):
    body = payload or {}
    try:
        row, page = manager.require_page(db, session_id, None)
        data, content_type, width, height = await page.screenshot(
            full_page=bool(body.get("full_page")),
            selector=body.get("selector") or None,
            fmt=body.get("format") or "png",
        )
        shot = screenshots.save_screenshot(
            db, row, data=data, content_type=content_type, width=width, height=height
        )
        manager.touch(db, row)
    except BrowserError as error:
        raise _http(error) from error
    db.commit()
    return {"session_id": row.id, **shot}


@router.post("/sessions/{session_id}/close")
async def close_session(session_id: str, db: Session = Depends(get_db)):
    try:
        await manager.close_session(db, session_id, None)
    except BrowserError as error:
        raise _http(error) from error
    db.commit()
    row = db.get(BrowserSession, session_id)
    names = _key_names(db, [row]) if row else {}
    return _payload(db, row, names) if row else {"closed": True}

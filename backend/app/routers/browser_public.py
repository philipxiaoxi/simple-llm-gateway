from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.capabilities.base import CapabilityError
from app.capabilities.browser import screenshots, tokens
from app.capabilities.runtime import invoke_capability
from app.clock import utcnow
from app.db import get_db
from app.models import BrowserScreenshot
from app.services.mcp_auth import get_mcp_key_from_headers

router = APIRouter(prefix="/v1/browser", tags=["browser"])


def _error(status: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"type": error_type, "message": message}})


def _mcp_key_dep(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    return get_mcp_key_from_headers(db, authorization, x_api_key)


async def _invoke(db: Session, mcp_key: Any, operation: str, payload: dict[str, Any]):
    try:
        return await invoke_capability(
            db, mcp_key=mcp_key, capability_id="browser", operation=operation, payload=payload
        )
    except CapabilityError as error:
        return _error(error.status_code, error.error_type, error.message)


@router.post("/sessions", status_code=201)
async def create_session(
    payload: dict[str, Any] | None = None,
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    return await _invoke(db, mcp_key, "open", payload or {})


@router.get("/sessions")
async def list_sessions(db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    return await _invoke(db, mcp_key, "list", {})


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str, db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)
):
    return await _invoke(db, mcp_key, "status", {"session_id": session_id})


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str, db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)
):
    return await _invoke(db, mcp_key, "close", {"session_id": session_id})


@router.post("/sessions/{session_id}/actions/{action}")
async def run_action(
    session_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    body = dict(payload or {})
    body["session_id"] = session_id
    return await _invoke(db, mcp_key, action, body)


@router.get("/screenshots/{screenshot_id}")
def get_screenshot(
    screenshot_id: str,
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    resolved = tokens.screenshot_id_from_token(token)
    if not resolved or resolved != screenshot_id:
        return _error(401, "authentication_error", "链接无效或已过期")
    row = db.get(BrowserScreenshot, screenshot_id)
    if row is None:
        return _error(404, "not_found", "截图不存在")
    if row.expires_at is not None and row.expires_at < utcnow():
        return _error(410, "expired", "截图已过期")
    path = screenshots.screenshot_path(row.session_id, row.filename)
    if not path.is_file():
        return _error(410, "expired", "截图已过期")
    return FileResponse(
        path,
        media_type=row.content_type or "image/png",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=300",
        },
    )

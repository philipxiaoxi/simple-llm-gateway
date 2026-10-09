"""公共访问口令门禁的通用接口（资讯公开页 / 离线下载公开页各自独立）。

路径为 `/api/public/access/{scope}/...`，scope ∈ info / offline。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.login_gate import LoginLocked
from app.services import info_public_gate as gate

router = APIRouter(prefix="/api/public/access", tags=["public-access"])


class UnlockBody(BaseModel):
    password: str = ""


def _ensure_scope(scope: str) -> None:
    if scope not in gate.SCOPES:
        raise HTTPException(status_code=404, detail="未知的门禁范围")


def _is_secure(request: Request) -> bool:
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    scheme = forwarded or request.url.scheme
    return scheme == "https"


def gate_status(scope: str, request: Request, db: Session = Depends(get_db)) -> dict:
    _ensure_scope(scope)
    token = request.cookies.get(gate.cookie_name(scope))
    row = gate.get_public_gate_settings(db, scope)
    required = gate.is_required(row)
    unlocked = (not required) or gate.verify_token(db, token, scope)
    watermark = gate.watermark_from_token(token, scope) if (required and unlocked) else None
    return {"scope": scope, "required": required, "unlocked": unlocked, "watermark": watermark}


def unlock_access(
    scope: str,
    payload: UnlockBody,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    _ensure_scope(scope)
    row = gate.get_public_gate_settings(db, scope)
    if not gate.is_required(row):
        return {"ok": True, "scope": scope, "required": False}

    client = request.client.host if request.client else "unknown"
    try:
        gate.unlock_gate.check(client)
    except LoginLocked as error:
        raise HTTPException(status_code=429, detail="尝试次数过多，请稍后再试") from error

    if not gate.check_password(row, payload.password):
        gate.unlock_gate.fail(client)
        raise HTTPException(status_code=401, detail="口令错误")

    gate.unlock_gate.succeed(client)
    user_agent = request.headers.get("user-agent", "")
    token, ttl, code = gate.issue_session(db, row, ip=client, user_agent=user_agent)
    response.set_cookie(
        key=gate.cookie_name(scope),
        value=token,
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=_is_secure(request),
        path="/",
    )
    return {"ok": True, "scope": scope, "required": True, "expires_in": ttl, "watermark": {"code": code}}


def lock_access(scope: str, response: Response) -> dict:
    _ensure_scope(scope)
    response.delete_cookie(gate.cookie_name(scope), path="/")
    return {"ok": True, "scope": scope}


router.add_api_route("/{scope}/gate", gate_status, methods=["GET"])
router.add_api_route("/{scope}/unlock", unlock_access, methods=["POST"])
router.add_api_route("/{scope}/lock", lock_access, methods=["POST"])

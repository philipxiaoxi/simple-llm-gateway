"""资讯公开页：无需登录的只读接口。

只下发内容本身，剥离渠道来源（source / author_name / permalink）与后台字段，
默认只返回精选；显式 `featured=false` 时返回全部（仍排除被判定为广告而隐藏的条目）。

公开页可开启口令门禁：解锁后签发 3 天有效的 HttpOnly Cookie，过期需重新输入。
未开启门禁时所有接口保持匿名可访问。
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.info import items
from app.login_gate import LoginLocked
from app.models import InfoItem
from app.services import info_public_gate as gate

router = APIRouter(prefix="/api/public/info", tags=["info-public"])


def require_public_gate(
    token: str | None = Cookie(default=None, alias=gate.COOKIE_NAME),
    db: Session = Depends(get_db),
) -> None:
    row = gate.get_public_gate_settings(db)
    if not gate.is_required(row):
        return
    if not gate.verify_token(db, token):
        raise HTTPException(
            status_code=401,
            detail={"error": {"type": "gate_required", "message": "需要访问口令"}},
        )


class UnlockBody(BaseModel):
    password: str = ""


def _is_secure(request: Request) -> bool:
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    scheme = forwarded or request.url.scheme
    return scheme == "https"


@router.get("/gate")
def public_gate_status(
    token: str | None = Cookie(default=None, alias=gate.COOKIE_NAME),
    db: Session = Depends(get_db),
):
    row = gate.get_public_gate_settings(db)
    required = gate.is_required(row)
    unlocked = (not required) or gate.verify_token(db, token)
    watermark = gate.watermark_from_token(token) if (required and unlocked) else None
    return {"required": required, "unlocked": unlocked, "watermark": watermark}


@router.post("/unlock")
def unlock(
    payload: UnlockBody,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    row = gate.get_public_gate_settings(db)
    if not gate.is_required(row):
        return {"ok": True, "required": False}

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
        key=gate.COOKIE_NAME,
        value=token,
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=_is_secure(request),
        path="/",
    )
    return {"ok": True, "required": True, "expires_in": ttl, "watermark": {"code": code}}


@router.post("/lock")
def lock(response: Response):
    response.delete_cookie(gate.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/items", dependencies=[Depends(require_public_gate)])
def list_public_items(
    cursor: str | None = None,
    limit: int = Query(default=items.DEFAULT_LIMIT, ge=1, le=items.MAX_LIMIT),
    featured: bool = True,
    kind: str | None = None,
    q: str | None = None,
    order: str = "desc",
    db: Session = Depends(get_db),
):
    rows, next_cursor, total = items.list_items(
        db,
        cursor=cursor,
        limit=limit,
        kind=kind,
        query=q,
        include_hidden=False,
        featured=featured,
        order=order,
    )
    return {
        "items": [items.serialize_item(row, public=True) for row in rows],
        "next_cursor": next_cursor,
        "total": total,
    }


@router.get("/items/{item_id}", dependencies=[Depends(require_public_gate)])
def get_public_item(item_id: str, db: Session = Depends(get_db)):
    row = items.get_item(db, item_id)
    if row is None or row.is_hidden:
        raise HTTPException(status_code=404, detail="内容不存在")
    return items.serialize_item(row, include_media=True, public=True)


@router.get("/stats", dependencies=[Depends(require_public_gate)])
def public_info_stats(db: Session = Depends(get_db)):
    visible = InfoItem.is_hidden.is_(False)
    item_count = int(
        db.scalar(select(func.count()).select_from(InfoItem).where(visible)) or 0
    )
    featured_count = int(
        db.scalar(
            select(func.count())
            .select_from(InfoItem)
            .where(visible, InfoItem.is_featured.is_(True))
        )
        or 0
    )
    return {"item_count": item_count, "featured_count": featured_count}

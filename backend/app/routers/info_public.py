"""资讯公开页：无需登录的只读接口。

只下发内容本身，剥离渠道来源（source / author_name / permalink）与后台字段，
默认只返回精选；显式 `featured=false` 时返回全部（仍排除被判定为广告而隐藏的条目）。

公开页可开启口令门禁：解锁后签发 3 天有效的 HttpOnly Cookie，过期需重新输入。
未开启门禁时所有接口保持匿名可访问。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.info import items
from app.models import InfoItem
from app.routers import public_gate
from app.services.info_public_gate import require_public_gate

router = APIRouter(prefix="/api/public/info", tags=["info-public"])


class UnlockBody(BaseModel):
    password: str = ""


# 兼容旧路径：门禁接口也挂在 /api/public/info 下（scope=info）
@router.get("/gate")
def info_gate(request: Request, db: Session = Depends(get_db)) -> dict:
    return public_gate.gate_status("info", request, db)


@router.post("/unlock")
def info_unlock(
    payload: UnlockBody,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    return public_gate.unlock_access("info", payload, request, response, db)


@router.post("/lock")
def info_lock(response: Response) -> dict:
    return public_gate.lock_access("info", response)


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

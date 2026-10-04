"""资讯收集的管理端接口。

全部挂在 `/api/admin/info` 下，用管理员 JWT 鉴权；只有媒体字节路由例外——浏览器
`<img>` / `<video>` 带不了 Authorization 头，那条路由用签名令牌鉴权。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_admin
from app.info import collector, items, sources, storage
from app.info import tokens as info_tokens
from app.info.errors import InfoError
from app.models import InfoItem, InfoMedia, InfoSource
from app.services.tikhub_config import tikhub_status

router = APIRouter(
    prefix="/api/admin/info",
    tags=["admin-info"],
    dependencies=[Depends(get_current_admin)],
)

# 媒体字节：公开路由，只靠签名令牌鉴权
media_router = APIRouter(prefix="/api/admin/info", tags=["admin-info-media"])


def _http(error: InfoError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"error": {"type": error.error_type, "message": error.message}},
    )


class PreviewBody(BaseModel):
    raw: str
    kind: str = "telegram"


class CreateBody(BaseModel):
    raw: str
    kind: str = "telegram"
    title: str | None = None
    poll_interval_seconds: int | None = None
    enabled: bool = True


class UpdateBody(BaseModel):
    title: str | None = None
    poll_interval_seconds: int | None = None
    enabled: bool | None = None


class StateBody(BaseModel):
    # 三种写法都接受：value（通用）、favorite/hidden（前端显式目标态）；都不传则切换
    value: bool | None = None
    favorite: bool | None = None
    hidden: bool | None = None


@router.get("/sources")
def list_sources(db: Session = Depends(get_db)):
    return {
        "sources": [sources.serialize_source(row) for row in sources.list_sources(db)],
        "provider": tikhub_status(db),
    }


@router.post("/sources/preview")
def preview_source(payload: PreviewBody, db: Session = Depends(get_db)):
    try:
        preview = sources.preview_source(db, payload.raw, kind=payload.kind)
    except InfoError as error:
        raise _http(error) from error
    already = (
        db.scalar(
            select(InfoSource.id).where(
                InfoSource.kind == payload.kind, InfoSource.identifier == preview.identifier
            )
        )
        is not None
    )
    return sources.serialize_preview(preview, already_added=already)


@router.post("/sources", status_code=201)
def create_source(payload: CreateBody, db: Session = Depends(get_db)):
    try:
        source = sources.create_source(
            db,
            raw=payload.raw,
            kind=payload.kind,
            title=payload.title,
            poll_interval_seconds=payload.poll_interval_seconds,
            enabled=payload.enabled,
        )
    except InfoError as error:
        raise _http(error) from error
    db.commit()
    return sources.serialize_source(source)


@router.patch("/sources/{source_id}")
def update_source(source_id: str, payload: UpdateBody, db: Session = Depends(get_db)):
    try:
        source = sources.get_source(db, source_id)
        sources.update_source(
            db,
            source,
            title=payload.title,
            poll_interval_seconds=payload.poll_interval_seconds,
            enabled=payload.enabled,
        )
    except InfoError as error:
        raise _http(error) from error
    db.commit()
    return sources.serialize_source(source)


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(
    source_id: str, purge_items: bool = Query(default=False), db: Session = Depends(get_db)
):
    """删除渠道。默认只删渠道、保留已采集内容与媒体文件；`?purge_items=true` 连带删除。"""
    try:
        source = sources.get_source(db, source_id)
        sources.delete_source(db, source, purge_items=purge_items)
    except InfoError as error:
        raise _http(error) from error
    db.commit()
    return None


@router.post("/sources/{source_id}/collect")
def collect_source(source_id: str, db: Session = Depends(get_db)):
    try:
        source = sources.get_source(db, source_id)
    except InfoError as error:
        raise _http(error) from error

    try:
        result = collector.collect_source(db, source, backfill=source.cursor_after is None)
    except InfoError as error:
        # 契约：采集失败（上游不可用等）仍返回 200，失败信息放在 error 字段，
        # 前端据此给出分类提示；渠道上的 last_error / 连续失败次数已在采集层落库。
        db.commit()
        return {
            "source_id": source_id,
            "fetched": 0,
            "created": 0,
            "skipped": 0,
            "error": {"type": error.error_type, "message": error.message},
        }
    db.commit()
    return result


@router.get("/items")
def list_items(
    cursor: str | None = None,
    limit: int = Query(default=items.DEFAULT_LIMIT, ge=1, le=items.MAX_LIMIT),
    source_id: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    favorite: bool = False,
    include_hidden: bool = False,
    order: str = "desc",
    db: Session = Depends(get_db),
):
    rows, next_cursor, total = items.list_items(
        db,
        cursor=cursor,
        limit=limit,
        source_id=source_id,
        kind=kind,
        query=q,
        favorite=favorite,
        include_hidden=include_hidden,
        order=order,
    )
    return {
        "items": [items.serialize_item(row) for row in rows],
        "next_cursor": next_cursor,
        "total": total,
    }


@router.get("/items/{item_id}")
def get_item(item_id: str, db: Session = Depends(get_db)):
    row = items.get_item(db, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="内容不存在")
    return items.serialize_item(row, include_media=True)


def _set_item_state(db: Session, item_id: str, field: str, value: bool | None):
    row = items.get_item(db, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="内容不存在")
    current = bool(getattr(row, field))
    setattr(row, field, (not current) if value is None else bool(value))
    db.commit()
    return items.serialize_item(row)


def _resolve_state(payload: StateBody | None, field: str) -> bool | None:
    if payload is None:
        return None
    specific = getattr(payload, field, None)
    if specific is not None:
        return bool(specific)
    return payload.value


@router.post("/items/{item_id}/favorite")
def set_favorite(item_id: str, payload: StateBody | None = None, db: Session = Depends(get_db)):
    return _set_item_state(db, item_id, "is_favorite", _resolve_state(payload, "favorite"))


@router.post("/items/{item_id}/hidden")
def set_hidden(item_id: str, payload: StateBody | None = None, db: Session = Depends(get_db)):
    return _set_item_state(db, item_id, "is_hidden", _resolve_state(payload, "hidden"))


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    return items.stats(db)


@media_router.get("/media/{media_id}")
def get_media(media_id: str, token: str = "", db: Session = Depends(get_db)):
    # 先验令牌再查库：未授权者无论 media_id 是否存在都拿到 401，不给枚举存在性的机会
    if not info_tokens.verify_token(media_id, token):
        raise HTTPException(status_code=401, detail="需要有效的媒体令牌")
    row = db.get(InfoMedia, media_id)
    if row is None:
        raise HTTPException(status_code=404, detail="媒体不存在")
    if row.purged or row.status != "ready":
        raise HTTPException(status_code=410, detail="媒体已不可用")
    item = db.get(InfoItem, row.item_id)
    if item is None or not row.filename:
        raise HTTPException(status_code=410, detail="媒体已不可用")
    path: Path = storage.media_path(item.id, row.filename)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="媒体文件不存在")
    return FileResponse(
        path,
        media_type=row.content_type or "application/octet-stream",
        headers={
            "Cache-Control": f"private, max-age={get_settings().info_media_token_ttl_seconds}",
            "X-Content-Type-Options": "nosniff",
        },
    )

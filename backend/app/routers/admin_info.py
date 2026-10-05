"""资讯收集的管理端接口。

全部挂在 `/api/admin/info` 下，用管理员 JWT 鉴权；只有媒体字节路由例外——浏览器
`<img>` / `<video>` 带不了 Authorization 头，那条路由用签名令牌鉴权。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_admin
from app.info import collector, items, sources, storage
from app.info import tokens as info_tokens
from app.info.errors import InfoError
from app.info.sanitize import sanitize_wechat_html
from app.models import InfoItem, InfoMedia, InfoSource, UpstreamAccount
from app.services import info_ai
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


class SaveArticleBody(BaseModel):
    url: str


class BatchIntervalBody(BaseModel):
    # ids 为空 / 不传表示全部渠道；poll_interval_seconds = 0 表示「手动触发」
    ids: list[str] | None = None
    poll_interval_seconds: int = 86400


class StateBody(BaseModel):
    # 三种写法都接受：value（通用）、favorite/hidden/featured（前端显式目标态）；都不传则切换
    value: bool | None = None
    favorite: bool | None = None
    hidden: bool | None = None
    featured: bool | None = None


class AiSettingsBody(BaseModel):
    enabled: bool | None = None
    account_id: int | None = None
    model: str | None = None
    vision_max_images: int | None = None
    max_image_bytes: int | None = None
    feature_threshold: int | None = None
    hide_ads: bool | None = None
    max_attempts: int | None = None
    prompt_template: str | None = None


class RescoreBody(BaseModel):
    # pending（默认，含已失败的）/ failed / all；也可只对指定 ids 重新判定
    scope: str = "pending"
    ids: list[str] | None = None


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


@router.post("/sources/batch-interval")
def batch_interval(payload: BatchIntervalBody, db: Session = Depends(get_db)):
    changed = sources.batch_update_interval(
        db, source_ids=payload.ids, poll_interval_seconds=payload.poll_interval_seconds
    )
    db.commit()
    return {"updated": changed}


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
        result = collector.collect_source(db, source, backfill=source.last_success_at is None)
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


@router.post("/sources/{source_id}/rebuild-content")
def rebuild_source_content(source_id: str, db: Session = Depends(get_db)):
    """重新补全该公众号条目的正文（含 HTML 排版）并重新判定。"""
    try:
        source = sources.get_source(db, source_id)
    except InfoError as error:
        raise _http(error) from error
    if source.kind != "wechat":
        raise HTTPException(status_code=400, detail="只有微信公众号渠道支持重新补全正文")
    reset = collector.reset_wechat_content(db, source)
    db.commit()
    return {"reset": reset}


@router.post("/articles", status_code=201)
def save_article(payload: SaveArticleBody, db: Session = Depends(get_db)):
    """手动保存单篇公众号文章，归到内置「其他」渠道（只调用一次详情接口）。"""
    try:
        adapter = collector.adapter_for(db, "wechat")
        url = adapter.normalize(payload.url)
        post, raw_html = adapter.fetch_article_post(url)
    except InfoError as error:
        db.rollback()
        raise _http(error) from error

    if not post.text.strip() and not post.media:
        raise HTTPException(status_code=422, detail="文章没有可用内容")

    cleaned = sanitize_wechat_html(raw_html)
    if cleaned and len(cleaned) > get_settings().info_wechat_html_max_chars:
        cleaned = ""

    source = sources.ensure_manual_source(db)
    item_id = collector.insert_article(db, source, post, content_html=cleaned or None)
    if item_id is None:
        db.rollback()
        raise HTTPException(status_code=409, detail="该文章已保存到「其他」")

    source.item_count = int(
        db.scalar(
            select(func.count()).select_from(InfoItem).where(InfoItem.source_id == source.id)
        )
        or 0
    )
    source.updated_at = utcnow()
    db.commit()
    return {"id": item_id, "source_id": source.id}


@router.get("/items")
def list_items(
    cursor: str | None = None,
    limit: int = Query(default=items.DEFAULT_LIMIT, ge=1, le=items.MAX_LIMIT),
    source_id: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    favorite: bool = False,
    featured: bool = False,
    include_hidden: bool = False,
    label: str | None = None,
    min_score: int | None = None,
    ai_status: str | None = None,
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
        featured=featured,
        label=label,
        min_score=min_score,
        ai_status=ai_status,
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


def _set_item_state(
    db: Session, item_id: str, field: str, value: bool | None, manual_field: str | None = None
):
    row = items.get_item(db, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="内容不存在")
    current = bool(getattr(row, field))
    setattr(row, field, (not current) if value is None else bool(value))
    if manual_field is not None:
        # 人工设定后，后续 AI 判定不再改写该字段
        setattr(row, manual_field, True)
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
    return _set_item_state(
        db, item_id, "is_hidden", _resolve_state(payload, "hidden"), manual_field="ai_hidden_manual"
    )


@router.post("/items/{item_id}/featured")
def set_featured(item_id: str, payload: StateBody | None = None, db: Session = Depends(get_db)):
    return _set_item_state(
        db,
        item_id,
        "is_featured",
        _resolve_state(payload, "featured"),
        manual_field="ai_featured_manual",
    )


@router.get("/ai/settings")
def get_ai_settings(db: Session = Depends(get_db)):
    row = info_ai.get_ai_settings(db)
    db.commit()
    return info_ai.serialize_settings(db, row)


@router.put("/ai/settings")
def update_ai_settings(payload: AiSettingsBody, db: Session = Depends(get_db)):
    row = info_ai.get_ai_settings(db)
    provided = payload.model_fields_set

    def bad(message: str) -> HTTPException:
        return HTTPException(
            status_code=400, detail={"error": {"type": "invalid_request", "message": message}}
        )

    if "account_id" in provided:
        if payload.account_id is not None and db.get(UpstreamAccount, payload.account_id) is None:
            raise bad("指定的上游账号不存在")
        row.account_id = payload.account_id
    if "enabled" in provided:
        row.enabled = bool(payload.enabled)
    if "model" in provided:
        row.model = (payload.model or "").strip()[:128]
    if "vision_max_images" in provided:
        value = int(payload.vision_max_images or 0)
        if value < 0 or value > 10:
            raise bad("图片张数上限需在 0 到 10 之间")
        row.vision_max_images = value
    if "max_image_bytes" in provided:
        value = int(payload.max_image_bytes or 0)
        if value <= 0:
            raise bad("单张图片字节上限必须为正数")
        row.max_image_bytes = value
    if "feature_threshold" in provided:
        value = int(payload.feature_threshold or 0)
        if value < 0 or value > 100:
            raise bad("精选阈值需在 0 到 100 之间")
        row.feature_threshold = value
    if "hide_ads" in provided:
        row.hide_ads = bool(payload.hide_ads)
    if "max_attempts" in provided:
        value = int(payload.max_attempts or 0)
        if value < 1 or value > 10:
            raise bad("重试上限需在 1 到 10 之间")
        row.max_attempts = value
    if "prompt_template" in provided:
        row.prompt_template = (payload.prompt_template or "").strip()
    if row.enabled and not row.account_id:
        raise bad("启用判定前必须选择上游账号")
    row.updated_at = utcnow()
    db.commit()
    return info_ai.serialize_settings(db, row)


@router.post("/ai/rescore")
def rescore_items(payload: RescoreBody, db: Session = Depends(get_db)):
    # 用一条批量 UPDATE，避免把整表 ORM 对象加载进内存
    statement = update(InfoItem).values(ai_status="pending", ai_attempts=0, ai_error="")
    if payload.ids:
        statement = statement.where(InfoItem.id.in_(payload.ids))
    elif payload.scope == "failed":
        statement = statement.where(InfoItem.ai_status == "failed")
    elif payload.scope == "all":
        pass
    else:
        statement = statement.where(InfoItem.ai_status.in_(("pending", "failed", "skipped")))
    result = db.execute(statement)
    db.commit()
    return {"count": int(result.rowcount or 0)}


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

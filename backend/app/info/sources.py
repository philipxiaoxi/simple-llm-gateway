"""采集渠道的增删改查。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.info import collector
from app.info.adapters import SourcePreview
from app.info.errors import InfoError
from app.models import InfoSource

DEFAULT_POLL_INTERVAL_SECONDS = 24 * 3600
MIN_POLL_INTERVAL_SECONDS = 300
MAX_POLL_INTERVAL_SECONDS = 7 * 24 * 3600
DEFAULT_KIND = "telegram"
# 手动保存的单篇文章统一归到这个内置渠道（不参与定时采集）
MANUAL_KIND = "manual"
MANUAL_IDENTIFIER = "other"
MANUAL_TITLE = "其他"


def clamp_interval(value: int | None) -> int:
    # 0（或负数）表示「手动触发」：不参与定时采集，只能手动采集
    if value is None:
        return DEFAULT_POLL_INTERVAL_SECONDS
    number = int(value)
    if number <= 0:
        return 0
    return max(MIN_POLL_INTERVAL_SECONDS, min(MAX_POLL_INTERVAL_SECONDS, number))


def list_sources(db: Session) -> list[InfoSource]:
    # 内置「其他」渠道固定排最后（不可编辑/删除，放末尾不干扰正常渠道）
    return list(
        db.scalars(
            select(InfoSource).order_by(
                case((InfoSource.kind == MANUAL_KIND, 1), else_=0), InfoSource.created_at
            )
        ).all()
    )


def get_source(db: Session, source_id: str) -> InfoSource:
    source = db.get(InfoSource, source_id)
    if source is None:
        raise InfoError("渠道不存在", status_code=404, error_type="source_not_found")
    return source


def preview_source(db: Session, raw: str, *, kind: str = DEFAULT_KIND) -> SourcePreview:
    adapter = collector.adapter_for(db, kind)
    return adapter.preview(adapter.normalize(raw))


def create_source(
    db: Session,
    *,
    raw: str,
    kind: str = DEFAULT_KIND,
    title: str | None = None,
    poll_interval_seconds: int | None = None,
    enabled: bool = True,
) -> InfoSource:
    adapter = collector.adapter_for(db, kind)
    identifier = adapter.normalize(raw)

    # 文章链接要先预览反查出真正的 username（微信）再判重；
    # 普通用户名可直接判重，重复添加不必白跑一次上游预览
    preview = None
    resolved = identifier
    if identifier.startswith(("http://", "https://")):
        preview = adapter.preview(identifier)
        resolved = (preview.identifier or identifier).strip() or identifier
    existing = db.scalar(
        select(InfoSource).where(InfoSource.kind == kind, InfoSource.identifier == resolved)
    )
    if existing is not None:
        raise InfoError("该渠道已在列表中", status_code=409, error_type="source_exists")
    if preview is None:
        preview = adapter.preview(resolved)
    identifier = resolved

    now = utcnow()
    source = InfoSource(
        id=str(uuid.uuid4()),
        kind=kind,
        identifier=identifier,
        title=(title or preview.title or identifier)[:256],
        username=(preview.username or "")[:128],
        description=preview.description or "",
        avatar_url=(preview.avatar_url or "")[:1024],
        subscriber_count_text=(preview.subscriber_count_text or "")[:16],
        enabled=enabled,
        poll_interval_seconds=clamp_interval(poll_interval_seconds),
        item_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    db.flush()
    return source


def _ensure_mutable(source: InfoSource) -> None:
    # 内置「其他」渠道固定存在，不允许修改配置或删除
    if source.kind == MANUAL_KIND:
        raise InfoError("内置「其他」分类不可修改", status_code=403, error_type="source_immutable")


def update_source(
    db: Session,
    source: InfoSource,
    *,
    title: str | None = None,
    poll_interval_seconds: int | None = None,
    enabled: bool | None = None,
) -> InfoSource:
    _ensure_mutable(source)
    if title is not None:
        cleaned = title.strip()
        if cleaned:
            source.title = cleaned[:256]
    if poll_interval_seconds is not None:
        source.poll_interval_seconds = clamp_interval(poll_interval_seconds)
    if enabled is not None:
        source.enabled = bool(enabled)
    source.updated_at = utcnow()
    db.flush()
    return source


def ensure_manual_source(db: Session) -> InfoSource:
    """取（必要时创建）内置「其他」渠道，用于存放手动保存的单篇文章。"""
    source = db.scalar(
        select(InfoSource).where(
            InfoSource.kind == MANUAL_KIND, InfoSource.identifier == MANUAL_IDENTIFIER
        )
    )
    if source is not None:
        return source
    now = utcnow()
    source = InfoSource(
        id=str(uuid.uuid4()),
        kind=MANUAL_KIND,
        identifier=MANUAL_IDENTIFIER,
        title=MANUAL_TITLE,
        username="",
        description="",
        avatar_url="",
        subscriber_count_text="",
        enabled=True,
        poll_interval_seconds=0,
        item_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    db.flush()
    return source


def batch_update_interval(
    db: Session, *, source_ids: list[str] | None, poll_interval_seconds: int
) -> int:
    """批量设置采集间隔；`source_ids` 为空表示全部渠道。返回受影响行数。"""
    interval = clamp_interval(poll_interval_seconds)
    statement = update(InfoSource).values(
        poll_interval_seconds=interval, updated_at=utcnow()
    )
    if source_ids:
        statement = statement.where(InfoSource.id.in_(list(source_ids)))
    # 内置「其他」渠道不参与定时采集，跳过其配置
    statement = statement.where(InfoSource.kind != MANUAL_KIND)
    result = db.execute(statement)
    db.flush()
    return int(result.rowcount or 0)


def delete_source(db: Session, source: InfoSource, *, purge_items: bool = False) -> None:
    """删除渠道。`purge_items=False` 默认保留已采集内容与媒体文件。"""
    _ensure_mutable(source)
    collector.delete_source(db, source, purge_items=purge_items)


def serialize_source(source: InfoSource) -> dict[str, Any]:
    return {
        "id": source.id,
        "kind": source.kind,
        "identifier": source.identifier,
        "title": source.title,
        "username": source.username,
        "description": source.description,
        "avatar_url": source.avatar_url,
        "subscriber_count_text": source.subscriber_count_text,
        "enabled": bool(source.enabled),
        "poll_interval_seconds": int(source.poll_interval_seconds or 0),
        "cursor_after": source.cursor_after,
        "last_polled_at": source.last_polled_at.isoformat() if source.last_polled_at else None,
        "last_success_at": source.last_success_at.isoformat() if source.last_success_at else None,
        "last_error": source.last_error,
        "consecutive_failures": int(source.consecutive_failures or 0),
        "item_count": int(source.item_count or 0),
        "created_at": source.created_at.isoformat() if source.created_at else None,
    }


def serialize_preview(preview: SourcePreview, *, already_added: bool = False) -> dict[str, Any]:
    return {
        "identifier": preview.identifier,
        "title": preview.title,
        "username": preview.username,
        "description": preview.description,
        "avatar_url": preview.avatar_url,
        "subscriber_count_text": preview.subscriber_count_text,
        "already_added": already_added,
    }


def due_sources(db: Session) -> list[InfoSource]:
    """按各自间隔 + 失败退避，挑出这一轮该采集的渠道。"""
    now = utcnow()
    due: list[InfoSource] = []
    for source in list_sources(db):
        if not source.enabled or source.kind == MANUAL_KIND:
            continue
        interval = clamp_interval(source.poll_interval_seconds)
        # 0 = 手动触发，不参与定时轮询
        if interval <= 0:
            continue
        failures = int(source.consecutive_failures or 0)
        if failures:
            # 连续失败时指数退避，最多放大到 8 倍，避免上游故障时持续打点
            interval = interval * min(2**failures, 8)
        if source.last_polled_at is None:
            due.append(source)
            continue
        elapsed = (now - source.last_polled_at).total_seconds()
        if elapsed >= interval:
            due.append(source)
    return due

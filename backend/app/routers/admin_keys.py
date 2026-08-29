from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session, joinedload, selectinload

from app.clock import utcnow
from app.config import get_settings
from app.crypto import decrypt_secret, encrypt_secret, generate_api_key, hash_api_key, key_prefix
from app.db import get_db
from app.deps import get_current_admin
from app.models import ApiKey, ApiKeyAccount, RequestLog
from app.schemas import CcSwitchBuildRequest, KeyCreate, KeyOut, KeyUpdate
from app.serializers import key_to_out
from app.services.ccswitch import (
    CCS_SWITCH_TARGETS,
    build_ccswitch_url_for_app,
    build_vscode_config,
    describe_ccswitch_targets,
)
from app.services.key_models import public_model_ids, replace_key_accounts

router = APIRouter(prefix="/api/admin/keys", tags=["admin-keys"], dependencies=[Depends(get_current_admin)])

KeySort = Literal["created_at", "tokens", "last_used"]


@router.get("", response_model=list[KeyOut])
def list_keys(
    sort: KeySort = Query(default="last_used"),
    db: Session = Depends(get_db),
) -> list[KeyOut]:
    rows = list(db.scalars(select(ApiKey).options(*_key_load_options())).unique().all())
    usage = _token_usage_by_key(db, [row.id for row in rows])
    rows.sort(key=lambda item: _sort_value(item, sort, usage), reverse=True)
    return [
        key_to_out(
            row,
            today_tokens=usage.get(row.id, (0, 0))[0],
            total_tokens=usage.get(row.id, (0, 0))[1],
        )
        for row in rows
    ]


@router.post("", response_model=KeyOut)
def create_key(payload: KeyCreate, db: Session = Depends(get_db)) -> KeyOut:
    plaintext = generate_api_key()
    settings = get_settings()
    item = ApiKey(
        name=payload.name,
        key_hash=hash_api_key(plaintext),
        key_encrypted=encrypt_secret(plaintext, settings.app_secret_key),
        key_prefix=key_prefix(plaintext),
    )
    db.add(item)
    db.flush()
    try:
        replace_key_accounts(db, item, payload.resolved_account_ids())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    db.flush()
    result = key_to_out(item, reveal=True, today_tokens=0, total_tokens=0)
    result.key = plaintext
    return result


@router.get("/{key_id}", response_model=KeyOut)
def get_key(key_id: int, db: Session = Depends(get_db)) -> KeyOut:
    item = _get_key(db, key_id)
    try:
        return _key_out(db, item, reveal=True)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/{key_id}/cc-switch")
def cc_switch_links(key_id: int, db: Session = Depends(get_db)) -> dict:
    item = _get_key(db, key_id)
    settings = get_settings()
    plaintext = _plaintext_key(item)
    display_name = _display_name(item)
    models = public_model_ids(item)
    return {
        "display_name": display_name,
        "models": models,
        "targets": describe_ccswitch_targets(settings.app_base_url, display_name, plaintext, models),
        "vscode": build_vscode_config(
            app_base_url=settings.app_base_url,
            display_name=display_name,
            api_key=plaintext,
            models=models,
        ),
    }


@router.post("/{key_id}/cc-switch")
def cc_switch_build(key_id: int, payload: CcSwitchBuildRequest, db: Session = Depends(get_db)) -> dict:
    item = _get_key(db, key_id)
    allowed = {item[0] for item in CCS_SWITCH_TARGETS}
    if payload.app not in allowed:
        raise HTTPException(status_code=400, detail="不支持的 CC Switch 应用")
    settings = get_settings()
    plaintext = _plaintext_key(item)
    display_name = _display_name(item)
    models = public_model_ids(item)
    try:
        url = build_ccswitch_url_for_app(
            app=payload.app,
            app_base_url=settings.app_base_url,
            display_name=display_name,
            api_key=plaintext,
            models=models,
            model=payload.model,
            haiku_model=payload.haiku_model,
            sonnet_model=payload.sonnet_model,
            opus_model=payload.opus_model,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"url": url}


@router.patch("/{key_id}", response_model=KeyOut)
def update_key(key_id: int, payload: KeyUpdate, db: Session = Depends(get_db)) -> KeyOut:
    item = _get_key(db, key_id)
    if payload.name is not None:
        item.name = payload.name
    if payload.status is not None:
        item.status = payload.status
    account_ids = payload.resolved_account_ids()
    if account_ids is not None:
        try:
            replace_key_accounts(db, item, account_ids)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    return _key_out(db, item)


@router.delete("/{key_id}")
def delete_key(key_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    item = _get_key(db, key_id)
    db.execute(
        update(RequestLog)
        .where(RequestLog.api_key_id == key_id)
        .values(api_key_name=func.coalesce(RequestLog.api_key_name, item.name))
    )
    db.delete(item)
    return {"ok": True}


def _key_load_options():
    return (
        joinedload(ApiKey.account),
        selectinload(ApiKey.account_links).selectinload(ApiKeyAccount.account),
    )


def _get_key(db: Session, key_id: int) -> ApiKey:
    item = db.scalar(select(ApiKey).options(*_key_load_options()).where(ApiKey.id == key_id))
    if item is None:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return item


def _plaintext_key(item: ApiKey) -> str:
    try:
        return decrypt_secret(item.key_encrypted, get_settings().app_secret_key)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def _display_name(item: ApiKey) -> str:
    accounts = item.account_links
    if len(accounts) > 1:
        return f"{item.name} · 多个上游账号"
    return f"{item.name} · {item.account.name}" if item.account else item.name


def _token_usage_by_key(db: Session, key_ids: list[int]) -> dict[int, tuple[int, int]]:
    if not key_ids:
        return {}
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.execute(
        select(
            RequestLog.api_key_id,
            func.coalesce(
                func.sum(case((RequestLog.created_at >= today, RequestLog.total_tokens), else_=0)),
                0,
            ),
            func.coalesce(func.sum(RequestLog.total_tokens), 0),
        )
        .where(RequestLog.api_key_id.in_(key_ids))
        .group_by(RequestLog.api_key_id)
    ).all()
    return {int(key_id): (int(today_tokens), int(total_tokens)) for key_id, today_tokens, total_tokens in rows}


def _key_out(db: Session, item: ApiKey, reveal: bool = False) -> KeyOut:
    today_tokens, total_tokens = _token_usage_by_key(db, [item.id]).get(item.id, (0, 0))
    return key_to_out(item, reveal=reveal, today_tokens=today_tokens, total_tokens=total_tokens)


def _sort_value(
    item: ApiKey,
    sort: KeySort,
    usage: dict[int, tuple[int, int]],
) -> tuple[datetime | int, int]:
    if sort == "tokens":
        return (usage.get(item.id, (0, 0))[1], item.id)
    if sort == "last_used":
        return (item.last_used_at or datetime.min, item.id)
    return (item.created_at, item.id)

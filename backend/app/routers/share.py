from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.db import get_db
from app.deps import resolve_api_key
from app.models import ApiKey, ModelAlias, RequestLog
from app.providers import find_provider
from app.schemas import (
    LeaderboardOut,
    ShareAliasDeleteRequest,
    ShareAliasRenameRequest,
    ShareAliasSaveRequest,
    ShareCcSwitchRequest,
    ShareLookupRequest,
)
from app.services.ccswitch import (
    CCS_SWITCH_TARGETS,
    build_ccswitch_url_for_app,
    build_vscode_config,
    describe_ccswitch_targets,
    gateway_endpoint,
)
from app.services.key_models import (
    ALIAS_ERROR,
    ALIAS_PATTERN,
    AliasConflict,
    account_prefix,
    active_bound_accounts,
    bound_accounts,
    build_model_catalog,
    is_account_available,
    public_model_ids,
    rename_model_alias,
)
from app.services.leaderboard import LeaderboardError, get_leaderboard
from app.services.model_caps import serialize_record

router = APIRouter(prefix="/api/share", tags=["share"])


def _require_key(db: Session, raw_key: str) -> ApiKey:
    item = resolve_api_key(db, raw_key.strip())
    if item is None:
        raise HTTPException(status_code=404, detail="未找到该 Key，请检查是否粘贴完整")
    return item


def _display_name(item: ApiKey) -> str:
    if len(item.account_links) > 1:
        return f"{item.name} · 多个上游账号"
    if item.account:
        return f"{item.name} · {item.account.name}"
    return item.name


def _account_availability_status(account) -> str:
    if account.source == "agent":
        return "online" if is_account_available(account) else "offline"
    return account.status


def _key_token_totals(db: Session, api_key_id: int) -> tuple[int, int]:
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_tokens = (
        db.scalar(
            select(func.coalesce(func.sum(RequestLog.total_tokens), 0)).where(
                RequestLog.api_key_id == api_key_id,
                RequestLog.created_at >= today,
            )
        )
        or 0
    )
    total_tokens = (
        db.scalar(
            select(func.coalesce(func.sum(RequestLog.total_tokens), 0)).where(
                RequestLog.api_key_id == api_key_id,
            )
        )
        or 0
    )
    return int(today_tokens), int(total_tokens)


def _serialize_aliases(item: ApiKey) -> list[dict]:
    rows = sorted(item.aliases or [], key=lambda row: row.id)
    return [{"alias": row.alias, "model": row.model_public_id} for row in rows]


@router.post("/lookup")
def lookup_key(payload: ShareLookupRequest, db: Session = Depends(get_db)) -> dict:
    raw_key = payload.api_key.strip()
    if len(raw_key) < 8:
        raise HTTPException(status_code=400, detail="请输入完整 API Key")
    item = _require_key(db, raw_key)
    settings = get_settings()
    account = item.account
    bound = bound_accounts(item)
    catalog = build_model_catalog(item)
    models = [entry.public_id for entry in catalog]
    records = []
    for entry in catalog:
        record = serialize_record(entry.record) if entry.record is not None else {}
        record["id"] = entry.public_id
        records.append(record)
    account_indexes = {bound_account.id: index for index, bound_account in enumerate(bound)}
    origin = settings.app_base_url.rstrip("/")
    provider = account.provider if account else ""
    registered = find_provider(provider) if provider else None
    today_tokens, total_tokens = _key_token_totals(db, item.id)
    return {
        "name": item.name,
        "account_name": account.name if account else "",
        "account_source": account.source if account else "upstream",
        "provider": provider,
        "provider_label": registered.label if registered else provider,
        "risk_level": account.risk_level if account else "low",
        "status": item.status,
        "account_status": _account_availability_status(account) if account else "",
        "accounts": [
            {
                "id": bound_account.id,
                "name": bound_account.name,
                "source": bound_account.source,
                "provider": bound_account.provider,
                "status": _account_availability_status(bound_account),
                "risk_level": bound_account.risk_level,
                "model_prefix": account_prefix(bound_account),
            }
            for bound_account in bound
        ],
        "today_tokens": today_tokens,
        "total_tokens": total_tokens,
        "models": models,
        "aliases": _serialize_aliases(item),
        "model_caps": records,
        "model_entries": [
            {
                "id": entry.public_id,
                "raw_id": entry.raw_id,
                "account_id": entry.account.id,
                "account_name": entry.account.name,
                "account_source": entry.account.source,
                "provider": entry.account.provider,
                "account_index": account_indexes.get(entry.account.id, 0),
            }
            for entry in catalog
        ],
        "gateway": {
            "origin": origin,
            "anthropic_base_url": gateway_endpoint(origin, False),
            "openai_base_url": gateway_endpoint(origin, True),
        },
        "targets": describe_ccswitch_targets(origin, _display_name(item), raw_key, models),
        "vscode": build_vscode_config(
            app_base_url=origin,
            display_name=_display_name(item),
            api_key=raw_key,
            models=models,
            records=records,
        ),
    }


@router.post("/cc-switch")
def build_share_cc_switch(payload: ShareCcSwitchRequest, db: Session = Depends(get_db)) -> dict:
    raw_key = payload.api_key.strip()
    item = _require_key(db, raw_key)
    if item.status != "active":
        raise HTTPException(status_code=403, detail="该 Key 已停用")
    if not active_bound_accounts(item):
        raise HTTPException(status_code=403, detail="绑定的上游账号不可用")
    allowed = {target[0] for target in CCS_SWITCH_TARGETS}
    if payload.app not in allowed:
        raise HTTPException(status_code=400, detail="不支持的 CC Switch 应用")
    settings = get_settings()
    models = public_model_ids(item)
    try:
        url = build_ccswitch_url_for_app(
            app=payload.app,
            app_base_url=settings.app_base_url,
            display_name=_display_name(item),
            api_key=raw_key,
            models=models,
            model=payload.model,
            haiku_model=payload.haiku_model,
            sonnet_model=payload.sonnet_model,
            opus_model=payload.opus_model,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"url": url}


@router.post("/aliases/save")
def save_alias(payload: ShareAliasSaveRequest, db: Session = Depends(get_db)) -> dict:
    item = _require_key(db, payload.api_key)
    if item.status != "active":
        raise HTTPException(status_code=403, detail="该 Key 已停用")
    alias = payload.alias.strip()
    if not ALIAS_PATTERN.match(alias):
        raise HTTPException(status_code=400, detail=ALIAS_ERROR)
    catalog_ids = {entry.public_id for entry in build_model_catalog(item, include_disabled=True)}
    if alias in catalog_ids:
        raise HTTPException(status_code=400, detail="别名不能与可用模型同名")
    if payload.model not in catalog_ids:
        raise HTTPException(status_code=400, detail="目标模型不在该 Key 的可用模型列表中")
    row = db.scalar(select(ModelAlias).where(ModelAlias.api_key_id == item.id, ModelAlias.alias == alias))
    if row is None:
        row = ModelAlias(api_key_id=item.id, alias=alias, model_public_id=payload.model)
        db.add(row)
    else:
        row.model_public_id = payload.model
        row.updated_at = utcnow()
    db.commit()
    return {"aliases": _serialize_aliases(item)}


@router.post("/aliases/rename")
def rename_alias(payload: ShareAliasRenameRequest, db: Session = Depends(get_db)) -> dict:
    item = _require_key(db, payload.api_key)
    if item.status != "active":
        raise HTTPException(status_code=403, detail="该 Key 已停用")
    try:
        rename_model_alias(db, item, payload.old_alias, payload.new_alias)
    except AliasConflict as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    db.commit()
    return {"aliases": _serialize_aliases(item)}


@router.post("/aliases/delete")
def delete_alias(payload: ShareAliasDeleteRequest, db: Session = Depends(get_db)) -> dict:
    item = _require_key(db, payload.api_key)
    row = db.scalar(
        select(ModelAlias).where(ModelAlias.api_key_id == item.id, ModelAlias.alias == payload.alias.strip())
    )
    if row is None:
        raise HTTPException(status_code=404, detail="别名不存在")
    db.delete(row)
    db.commit()
    return {"aliases": _serialize_aliases(item)}


@router.get("/leaderboard", response_model=LeaderboardOut)
async def public_leaderboard(db: Session = Depends(get_db)) -> LeaderboardOut:
    try:
        payload = await get_leaderboard(db, force=False, public=True)
    except LeaderboardError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return LeaderboardOut.model_validate(payload)

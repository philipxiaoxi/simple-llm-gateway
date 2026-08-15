from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import resolve_api_key
from app.models import ApiKey
from app.providers import PRESETS
from app.schemas import ShareCcSwitchRequest, ShareLookupRequest
from app.services.ccswitch import (
    CCS_SWITCH_TARGETS,
    build_ccswitch_url_for_app,
    describe_ccswitch_targets,
    gateway_endpoint,
    parse_models_json,
)

router = APIRouter(prefix="/api/share", tags=["share"])


def _require_key(db: Session, raw_key: str) -> ApiKey:
    item = resolve_api_key(db, raw_key.strip())
    if item is None:
        raise HTTPException(status_code=404, detail="未找到该 Key，请检查是否粘贴完整")
    return item


def _display_name(item: ApiKey) -> str:
    if item.account:
        return f"{item.name} · {item.account.name}"
    return item.name


@router.post("/lookup")
def lookup_key(payload: ShareLookupRequest, db: Session = Depends(get_db)) -> dict:
    raw_key = payload.api_key.strip()
    if len(raw_key) < 8:
        raise HTTPException(status_code=400, detail="请输入完整 API Key")
    item = _require_key(db, raw_key)
    settings = get_settings()
    account = item.account
    models = parse_models_json(account.models_json if account else None)
    origin = settings.app_base_url.rstrip("/")
    provider = account.provider if account else ""
    return {
        "name": item.name,
        "account_name": account.name if account else "",
        "provider": provider,
        "provider_label": PRESETS.get(provider, {}).get("label") or provider,
        "status": item.status,
        "account_status": account.status if account else "",
        "models": models,
        "gateway": {
            "origin": origin,
            "anthropic_base_url": gateway_endpoint(origin, False),
            "openai_base_url": gateway_endpoint(origin, True),
        },
        "targets": describe_ccswitch_targets(origin, _display_name(item), raw_key, models),
    }


@router.post("/cc-switch")
def build_share_cc_switch(payload: ShareCcSwitchRequest, db: Session = Depends(get_db)) -> dict:
    raw_key = payload.api_key.strip()
    item = _require_key(db, raw_key)
    if item.status != "active":
        raise HTTPException(status_code=403, detail="该 Key 已停用")
    if item.account is None or item.account.status != "active":
        raise HTTPException(status_code=403, detail="绑定的上游账号不可用")
    allowed = {target[0] for target in CCS_SWITCH_TARGETS}
    if payload.app not in allowed:
        raise HTTPException(status_code=400, detail="不支持的 CC Switch 应用")
    settings = get_settings()
    models = parse_models_json(item.account.models_json)
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

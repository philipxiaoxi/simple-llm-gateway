from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import extract_raw_api_key, resolve_api_key
from app.errors import protocol_error
from app.services.bridge import prepare_credential
from app.services.credentials import CredentialError
from app.services.key_models import (
    active_bound_accounts,
    build_model_catalog,
    resolve_alias_public_id,
    resolve_model,
)
from app.services.proxy import handle_chat, handle_count_tokens, list_models_payload

router = APIRouter(tags=["proxy"])


async def _authenticate(
    request: Request,
    db: Session,
    protocol: str,
    authorization: str | None,
    x_api_key: str | None,
):
    raw = extract_raw_api_key(authorization, x_api_key)
    api_key = resolve_api_key(db, raw)
    if api_key is None:
        return None, protocol_error(protocol, 401, "无效的 API Key")
    if api_key.status != "active":
        return None, protocol_error(protocol, 401, "API Key 已停用")
    active_accounts = active_bound_accounts(api_key)
    if not active_accounts:
        account = api_key.account
        if account is None:
            return None, protocol_error(protocol, 403, "绑定的上游账号不存在", "permission_error")
        if account.status == "disabled":
            return None, protocol_error(protocol, 403, "绑定的上游账号已停用，请联系管理员启用", "permission_error")
        return None, protocol_error(protocol, 403, f"绑定的上游账号当前不可用（状态：{account.status}）", "permission_error")
    return (api_key, active_accounts[0]), None


def _resolve_request_account(protocol: str, api_key, body: dict, db: Session):
    catalog = build_model_catalog(api_key, include_disabled=True)
    enabled_catalog = [
        entry for entry in catalog if entry.record is None or entry.record.enabled
    ]
    active_accounts = active_bound_accounts(api_key)
    single_account = active_accounts[0] if len(active_accounts) == 1 else None
    model = str(body.get("model") or "").strip()
    alias_name = ""
    if not catalog:
        # 别名指向的模型已被全部移除时不透传原始别名，明确报错
        if resolve_alias_public_id(db, api_key.id, model) is not None:
            return None, protocol_error(protocol, 400, f"别名“{model}”指向的模型不存在，请到自助查询页重新设置")
        if single_account is not None:
            return single_account, None
        return None, protocol_error(protocol, 400, "该 Key 没有可用的上游模型")
    if not model:
        return None, protocol_error(protocol, 400, "请求缺少模型名称")
    entry = resolve_model(enabled_catalog, model, single_account=None)
    if entry is None:
        # 真实模型名优先，其次按 Key 级别名改写为绑定的公开模型名
        alias_target = resolve_alias_public_id(db, api_key.id, model)
        if alias_target is not None:
            alias_name = model
            model = alias_target
            entry = resolve_model(enabled_catalog, model, single_account=None)
    if entry is None:
        disabled = resolve_model(catalog, model, single_account=None)
        if disabled is not None and disabled.record is not None and not disabled.record.enabled:
            return None, protocol_error(protocol, 400, f"模型“{model}”已关闭")
        if alias_name:
            return None, protocol_error(protocol, 400, f"别名“{alias_name}”指向的模型不存在，请到自助查询页重新设置")
        if single_account is not None:
            return None, protocol_error(protocol, 400, f"模型“{model}”不在该上游账号已配置的模型列表中")
        return None, protocol_error(protocol, 400, f"模型“{model}”不在该 Key 已配置的模型列表中")
    body["model"] = entry.raw_id
    return entry.account, None


async def _validate_upstream_credential(protocol: str, account, db: Session) -> JSONResponse | None:
    try:
        await prepare_credential(account, db)
    except (CredentialError, ValueError) as error:
        status_code = getattr(error, "status_code", 403)
        return protocol_error(protocol, status_code, str(error), "permission_error")
    return None


@router.api_route("/v1/chat/completions", methods=["POST"], response_model=None)
@router.api_route("/chat/completions", methods=["POST"], response_model=None)
async def chat_completions(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> JSONResponse | StreamingResponse:
    protocol = "openai_chat"
    resolved, error = await _authenticate(request, db, protocol, authorization, x_api_key)
    if error:
        return error
    api_key, _primary = resolved
    body = await request.json()
    account, error = _resolve_request_account(protocol, api_key, body, db)
    if error:
        return error
    error = await _validate_upstream_credential(protocol, account, db)
    if error:
        return error
    return await handle_chat(db, api_key, account, body, protocol, dict(request.headers))


@router.api_route("/v1/responses", methods=["POST"], response_model=None)
@router.api_route("/responses", methods=["POST"], response_model=None)
async def responses(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> JSONResponse | StreamingResponse:
    protocol = "openai_responses"
    resolved, error = await _authenticate(request, db, protocol, authorization, x_api_key)
    if error:
        return error
    api_key, _primary = resolved
    body = await request.json()
    account, error = _resolve_request_account(protocol, api_key, body, db)
    if error:
        return error
    error = await _validate_upstream_credential(protocol, account, db)
    if error:
        return error
    return await handle_chat(db, api_key, account, body, protocol, dict(request.headers))


@router.api_route("/v1/messages", methods=["POST"], response_model=None)
@router.api_route("/anthropic/v1/messages", methods=["POST"], response_model=None)
async def messages(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> JSONResponse | StreamingResponse:
    protocol = "anthropic_messages"
    resolved, error = await _authenticate(request, db, protocol, authorization, x_api_key)
    if error:
        return error
    api_key, _primary = resolved
    body = await request.json()
    account, error = _resolve_request_account(protocol, api_key, body, db)
    if error:
        return error
    error = await _validate_upstream_credential(protocol, account, db)
    if error:
        return error
    return await handle_chat(db, api_key, account, body, protocol, dict(request.headers))


@router.api_route("/v1/messages/count_tokens", methods=["POST"], response_model=None)
@router.api_route("/anthropic/v1/messages/count_tokens", methods=["POST"], response_model=None)
async def count_tokens(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> JSONResponse:
    protocol = "anthropic_messages"
    resolved, error = await _authenticate(request, db, protocol, authorization, x_api_key)
    if error:
        return error
    api_key, _primary = resolved
    body = await request.json()
    account, error = _resolve_request_account(protocol, api_key, body, db)
    if error:
        return error
    error = await _validate_upstream_credential(protocol, account, db)
    if error:
        return error
    return await handle_count_tokens(account, body, dict(request.headers))


@router.api_route("/v1/models", methods=["GET"], response_model=None)
@router.api_route("/models", methods=["GET"], response_model=None)
async def models(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> JSONResponse:
    protocol = "openai_chat"
    resolved, error = await _authenticate(request, db, protocol, authorization, x_api_key)
    if error:
        return error
    api_key, _primary = resolved
    return JSONResponse(list_models_payload(api_key))

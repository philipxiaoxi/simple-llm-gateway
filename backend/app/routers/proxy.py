from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import extract_raw_api_key, resolve_api_key
from app.errors import protocol_error
from app.services.bridge import prepare_credential
from app.services.ccswitch import parse_models_json
from app.services.credentials import CredentialError
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
    account = api_key.account
    if account is None:
        return None, protocol_error(protocol, 403, "绑定的上游账号不存在", "permission_error")
    if account.status == "disabled":
        return None, protocol_error(protocol, 403, "绑定的上游账号已停用，请联系管理员启用", "permission_error")
    if account.status != "active":
        return None, protocol_error(protocol, 403, f"绑定的上游账号当前不可用（状态：{account.status}）", "permission_error")
    return (api_key, account), None


def _validate_model(protocol: str, account, body: dict) -> JSONResponse | None:
    models = parse_models_json(account.models_json)
    if not models:
        return None
    model = str(body.get("model") or "").strip()
    if not model:
        return protocol_error(protocol, 400, "请求缺少模型名称")
    if model not in models:
        return protocol_error(protocol, 400, f"模型“{model}”不在该上游账号已配置的模型列表中")
    return None


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
    api_key, account = resolved
    body = await request.json()
    error = _validate_model(protocol, account, body)
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
    api_key, account = resolved
    body = await request.json()
    error = _validate_model(protocol, account, body)
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
    api_key, account = resolved
    body = await request.json()
    error = _validate_model(protocol, account, body)
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
    _api_key, account = resolved
    body = await request.json()
    error = _validate_model(protocol, account, body)
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
    _api_key, account = resolved
    return JSONResponse(list_models_payload(account))

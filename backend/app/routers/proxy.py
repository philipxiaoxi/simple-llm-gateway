from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import extract_raw_api_key, resolve_api_key
from app.errors import protocol_error
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
    if account is None or account.status != "active":
        return None, protocol_error(protocol, 403, "绑定的上游账号不可用", "permission_error")
    return (api_key, account), None


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
    return await handle_chat(db, api_key, account, body, protocol)


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
    return await handle_chat(db, api_key, account, body, protocol)


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
    return await handle_chat(db, api_key, account, body, protocol)


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
    return await handle_count_tokens(account, body)


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

from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db, get_session_factory
from app.deps import extract_raw_api_key, resolve_api_key
from app.errors import protocol_error
from app.services.proxy import handle_chat, handle_count_tokens, list_models_payload
from app.services.ratelimit import RateLimitTimeout, get_limiter

router = APIRouter(tags=["proxy"])


async def _authenticate(
    request: Request,
    db: Session,
    protocol: str,
    authorization: str | None,
    x_api_key: str | None,
):
    raw = extract_raw_api_key(authorization, x_api_key)
    # 用独立 Session 解析 API Key，用完立即关闭，避免限流等待期间占用连接池。
    # resolve_api_key 是同步数据库操作，放到线程池执行，避免阻塞事件循环
    # （高并发下同步 DB 操作会拖垮整个后端）。
    auth_session = get_session_factory()()
    try:
        api_key = await asyncio.to_thread(resolve_api_key, auth_session, raw)
        if api_key is None:
            return None, protocol_error(protocol, 401, "无效的 API Key")
        if api_key.status != "active":
            return None, protocol_error(protocol, 401, "API Key 已停用")
        account = api_key.account
        if account is None or account.status != "active":
            return None, protocol_error(protocol, 403, "绑定的上游账号不可用", "permission_error")
        return (api_key, account), None
    finally:
        auth_session.close()


async def _acquire_slot(request: Request, account, protocol: str):
    """获取限流槽位。无限制立即返回；超时返回 429 响应。

    等待期间若客户端断开连接，则取消等待并返回 (None, None)，
    避免无效请求继续占用槽位和上游资源。
    """
    limiter = get_limiter(account.id, account.rpm_limit)
    if limiter.capacity <= 0:
        return limiter, None
    acquire_task = asyncio.create_task(limiter.acquire())
    try:
        while not acquire_task.done():
            if await request.is_disconnected():
                acquire_task.cancel()
                with suppress(asyncio.CancelledError):
                    await acquire_task
                return None, None
            await asyncio.sleep(0.1)
        try:
            await acquire_task
        except RateLimitTimeout:
            return None, protocol_error(protocol, 429, "上游请求过多，请稍后再试")
        return limiter, None
    finally:
        if not acquire_task.done():
            acquire_task.cancel()
            with suppress(asyncio.CancelledError):
                await acquire_task


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
    limiter, error = await _acquire_slot(request, account, protocol)
    if error:
        return error
    if limiter is None:
        # 客户端已断开，无需继续处理
        return None
    return await handle_chat(
        db, api_key, account, body, protocol, dict(request.headers), release_slot=limiter.release
    )


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
    limiter, error = await _acquire_slot(request, account, protocol)
    if error:
        return error
    if limiter is None:
        # 客户端已断开，无需继续处理
        return None
    return await handle_chat(
        db, api_key, account, body, protocol, dict(request.headers), release_slot=limiter.release
    )


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
    limiter, error = await _acquire_slot(request, account, protocol)
    if error:
        return error
    if limiter is None:
        # 客户端已断开，无需继续处理
        return None
    return await handle_chat(
        db, api_key, account, body, protocol, dict(request.headers), release_slot=limiter.release
    )


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

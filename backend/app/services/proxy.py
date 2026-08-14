from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_session_factory
from app.errors import protocol_error
from app.models import ApiKey, RequestLog, UpstreamAccount
from app.providers import PRESETS
from app.services.bridge import (
    anthropic_to_openai_messages,
    call_chat,
    count_openai_tokens,
    extract_text_from_openai_chunk,
    extract_usage,
    input_to_messages,
    openai_response_to_anthropic,
    prepare_credential,
    responses_from_chat,
    stream_openai_chunks,
    _passthrough_keys,
)
from app.services.credentials import CredentialError


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def parse_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def save_log(
    db: Session,
    *,
    account_id: int,
    api_key_id: int,
    protocol: str,
    model: str | None,
    stream: bool,
    status: str,
    http_status: int,
    error_message: str | None,
    usage: tuple[int | None, int | None, int | None],
    latency_ms: int,
    request_body: Any,
    response_body: Any,
) -> RequestLog:
    prompt, completion, total = usage
    log = RequestLog(
        account_id=account_id,
        api_key_id=api_key_id,
        protocol=protocol,
        model=model,
        stream=stream,
        status=status,
        http_status=http_status,
        error_message=error_message,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        latency_ms=latency_ms,
        request_body=dump_json(request_body) if request_body is not None else None,
        response_body=dump_json(response_body) if response_body is not None else None,
    )
    db.add(log)
    db.flush()
    return log


async def handle_chat(
    db: Session,
    api_key: ApiKey,
    account: UpstreamAccount,
    body: dict[str, Any],
    protocol: str,
) -> JSONResponse | StreamingResponse:
    started = time.perf_counter()
    stream = bool(body.get("stream"))
    model = str(body.get("model") or "")
    try:
        credential = await prepare_credential(account, db)
    except (CredentialError, ValueError) as error:
        status_code = getattr(error, "status_code", 403)
        save_log(
            db,
            account_id=account.id,
            api_key_id=api_key.id,
            protocol=protocol,
            model=model,
            stream=stream,
            status="error",
            http_status=status_code,
            error_message=str(error),
            usage=(None, None, None),
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_body=body,
            response_body=None,
        )
        return protocol_error(protocol, status_code, str(error))

    if protocol == "anthropic_messages":
        messages = anthropic_to_openai_messages(body)
        extra = _passthrough_keys(body)
        if "max_tokens" not in extra and body.get("max_tokens"):
            extra["max_tokens"] = body["max_tokens"]
    elif protocol == "openai_responses":
        messages = input_to_messages(body)
        extra = _passthrough_keys(body)
    else:
        messages = body.get("messages") or []
        extra = _passthrough_keys(body)

    extra.pop("stream", None)

    if stream:
        return await _stream_response(
            db, api_key, account, body, protocol, model, messages, extra, credential, started
        )

    try:
        result = await call_chat(account, messages, model, False, extra, credential)
    except Exception as error:
        return _fail(db, api_key, account, body, protocol, model, stream, started, error)

    if hasattr(result, "model_dump"):
        openai_payload = result.model_dump()
    else:
        openai_payload = result if isinstance(result, dict) else {}

    if protocol == "anthropic_messages":
        payload = openai_response_to_anthropic(openai_payload, model)
    elif protocol == "openai_responses":
        payload = responses_from_chat(openai_payload, model)
    else:
        payload = openai_payload

    usage = extract_usage(openai_payload if protocol != "anthropic_messages" else {"usage": {
        "prompt_tokens": payload.get("usage", {}).get("input_tokens"),
        "completion_tokens": payload.get("usage", {}).get("output_tokens"),
    }})
    save_log(
        db,
        account_id=account.id,
        api_key_id=api_key.id,
        protocol=protocol,
        model=model,
        stream=False,
        status="success",
        http_status=200,
        error_message=None,
        usage=usage,
        latency_ms=int((time.perf_counter() - started) * 1000),
        request_body=body,
        response_body=payload,
    )
    api_key.last_used_at = datetime.utcnow()
    return JSONResponse(payload)


async def _stream_response(
    db: Session,
    api_key: ApiKey,
    account: UpstreamAccount,
    body: dict[str, Any],
    protocol: str,
    model: str,
    messages: list[dict[str, Any]],
    extra: dict[str, Any],
    credential: str,
    started: float,
) -> StreamingResponse | JSONResponse:
    try:
        result = await call_chat(account, messages, model, True, extra, credential)
    except Exception as error:
        return _fail(db, api_key, account, body, protocol, model, True, started, error)

    message_id = f"msg_{uuid.uuid4().hex}"
    account_id = account.id
    api_key_id = api_key.id

    async def event_source() -> AsyncIterator[bytes]:
        collected = ""
        usage = (None, None, None)
        error_text: str | None = None
        status = "success"
        http_status = 200
        response_body: Any = None
        try:
            if protocol == "anthropic_messages":
                yield _sse(
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": message_id,
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": model,
                            "stop_reason": None,
                            "usage": {"input_tokens": 0, "output_tokens": 0},
                        },
                    },
                )
                yield _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            async for chunk in stream_openai_chunks(result):
                usage_candidate = extract_usage(chunk)
                if any(part is not None for part in usage_candidate):
                    usage = usage_candidate
                text = extract_text_from_openai_chunk(chunk)
                collected += text
                if protocol == "anthropic_messages":
                    if text:
                        yield _sse(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {"type": "text_delta", "text": text},
                            },
                        )
                elif protocol == "openai_responses":
                    if text:
                        event = {
                            "type": "response.output_text.delta",
                            "delta": text,
                        }
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
                else:
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
            if protocol == "anthropic_messages":
                yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
                yield _sse(
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {
                            "output_tokens": usage[1] or 0,
                        },
                    },
                )
                yield _sse("message_stop", {"type": "message_stop"})
                response_body = {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": collected}],
                    "model": model,
                    "stop_reason": "end_turn",
                    "usage": {
                        "input_tokens": usage[0] or 0,
                        "output_tokens": usage[1] or 0,
                    },
                }
            elif protocol == "openai_responses":
                completed = {
                    "type": "response.completed",
                    "response": {
                        "id": f"resp_{uuid.uuid4().hex}",
                        "status": "completed",
                        "model": model,
                        "output_text": collected,
                    },
                }
                yield f"data: {json.dumps(completed, ensure_ascii=False)}\n\n".encode()
                response_body = completed["response"]
            else:
                yield b"data: [DONE]\n\n"
                response_body = {"choices": [{"message": {"role": "assistant", "content": collected}}]}
        except Exception as error:
            status = "error"
            http_status = 502
            error_text = str(error)
            if protocol == "anthropic_messages":
                yield _sse(
                    "error",
                    {"type": "error", "error": {"type": "api_error", "message": error_text}},
                )
            else:
                yield f"data: {json.dumps({'error': {'message': error_text}}, ensure_ascii=False)}\n\n".encode()
        finally:
            session = get_session_factory()()
            try:
                save_log(
                    session,
                    account_id=account_id,
                    api_key_id=api_key_id,
                    protocol=protocol,
                    model=model,
                    stream=True,
                    status=status,
                    http_status=http_status,
                    error_message=error_text,
                    usage=usage,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    request_body=body,
                    response_body=response_body,
                )
                stored_key = session.get(ApiKey, api_key_id)
                if stored_key is not None:
                    stored_key.last_used_at = datetime.utcnow()
                session.commit()
            finally:
                session.close()

    return StreamingResponse(event_source(), media_type="text/event-stream")


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def _fail(
    db: Session,
    api_key: ApiKey,
    account: UpstreamAccount,
    body: dict[str, Any],
    protocol: str,
    model: str,
    stream: bool,
    started: float,
    error: Exception,
) -> JSONResponse:
    message = str(error)
    status_code = 504 if "timeout" in message.lower() else 502
    save_log(
        db,
        account_id=account.id,
        api_key_id=api_key.id,
        protocol=protocol,
        model=model,
        stream=stream,
        status="error",
        http_status=status_code,
        error_message=message,
        usage=(None, None, None),
        latency_ms=int((time.perf_counter() - started) * 1000),
        request_body=body,
        response_body=None,
    )
    return protocol_error(protocol, status_code, message)


async def handle_count_tokens(
    account: UpstreamAccount,
    body: dict[str, Any],
) -> JSONResponse:
    messages = anthropic_to_openai_messages(body)
    model = str(body.get("model") or "claude")
    tokens = await count_openai_tokens(model, messages)
    return JSONResponse({"input_tokens": tokens})


def list_models_payload(account: UpstreamAccount) -> dict[str, Any]:
    models = PRESETS.get(account.provider, {}).get("models") or []
    return {
        "object": "list",
        "data": [
            {
                "id": name,
                "object": "model",
                "created": int(time.time()),
                "owned_by": account.provider,
            }
            for name in models
        ],
    }

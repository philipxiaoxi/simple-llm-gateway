from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.db import get_session_factory
from app.errors import protocol_error
from app.models import ApiKey, RequestLog, UpstreamAccount
from app.providers import get_provider
from app.services.key_models import build_model_catalog
from app.services.model_caps import serialize_record
from app.services.bridge import (
    AnthropicStreamTranslator,
    ResponsesStreamCollector,
    anthropic_extra_to_openai,
    anthropic_to_openai_messages,
    call_chat,
    call_responses,
    count_openai_tokens,
    ensure_stream_usage,
    estimate_usage,
    extract_reasoning_from_openai_chunk,
    extract_text_from_openai_chunk,
    extract_usage,
    input_to_messages,
    pick_usage_from_chunk,
    openai_response_to_anthropic,
    prepare_credential,
    reasoning_map_from_responses_payload,
    responses_extra_passthrough,
    responses_event_to_dict,
    responses_payload_to_dict,
    responses_sse,
    sanitize_responses_input,
    sse_event,
    stream_openai_chunks,
    _passthrough_keys,
)
from app.services.conversation import (
    append_log_messages,
    extract_assistant_message,
    extract_request_messages,
    extract_session_key,
    find_continuation_log,
    load_log_messages_head,
    load_log_messages_tail,
    new_messages_to_store,
)
from app.services.credentials import CredentialError
from app.services.reasoning import (
    dump_reasoning_json,
    inject_reasoning_into_messages,
    load_reasoning_map,
    merge_reasoning_maps,
    parse_reasoning_json,
    reasoning_map_from_anthropic_content,
    reasoning_map_from_anthropic_messages,
    reasoning_map_from_messages,
    reasoning_map_from_openai_payload,
)


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def billed_total(prompt: int | None, completion: int | None, total: int | None) -> int | None:
    if total is not None:
        return total
    if prompt is not None or completion is not None:
        return (prompt or 0) + (completion or 0)
    return None


def accumulate_usage(
    stored: tuple[int | None, int | None, int | None],
    incoming: tuple[int | None, int | None, int | None],
) -> tuple[int | None, int | None, int | None]:
    stored_prompt, stored_completion, stored_total = stored
    prompt, completion, total = incoming
    billed = billed_total(prompt, completion, total)
    next_prompt = stored_prompt if prompt is None else (stored_prompt or 0) + prompt
    next_completion = stored_completion if completion is None else (stored_completion or 0) + completion
    if billed is not None:
        next_total = (stored_total or 0) + billed
    elif next_prompt is not None or next_completion is not None:
        next_total = (next_prompt or 0) + (next_completion or 0)
    else:
        next_total = stored_total
    return next_prompt, next_completion, next_total


def _finalize_stream_log(
    *,
    account_id: int,
    api_key_id: int,
    protocol: str,
    model: str,
    status: str,
    http_status: int,
    error_text: str | None,
    usage: tuple[int | None, int | None, int | None],
    started: float,
    request_body: Any,
    response_body: Any,
    request_headers: dict[str, str] | None,
    reasoning_map: dict[str, str],
) -> None:
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
            latency_ms=elapsed_ms(started),
            request_body=request_body,
            response_body=response_body,
            request_headers=request_headers,
            reasoning_map=reasoning_map,
        )
        stored_key = session.get(ApiKey, api_key_id)
        if stored_key is not None:
            stored_key.last_used_at = utcnow()
        session.commit()
    finally:
        session.close()


def _output_text_from_openai(payload: dict[str, Any]) -> str:
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif content is None:
        text = ""
    else:
        text = json.dumps(content, ensure_ascii=False)
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        text = f"{reasoning}\n{text}" if text else reasoning
    return text


def _stream_output_text(protocol: str, collector: Any, translator: Any) -> str:
    if protocol == "anthropic_messages":
        parts: list[str] = []
        if translator.reasoning:
            parts.append(translator.reasoning)
        if translator.text:
            parts.append(translator.text)
        for tool in translator.tools:
            parts.append(str(tool.get("arguments") or ""))
        return "\n".join(parts)
    return f"{collector.reasoning}{collector.text}"


def reconstruct_anthropic_from_sse(sse_text: str, model: str) -> dict[str, Any]:
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_blocks: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    stop_reason = "end_turn"
    message_id = None
    current_tool: dict[str, Any] | None = None
    for match in re.finditer(r"data:\s*(\{.*\})", sse_text):
        try:
            event = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        if event_type == "message_start":
            message_id = (event.get("message") or {}).get("id")
        elif event_type == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                current_tool = {
                    "type": "tool_use",
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "input": block.get("input") or {},
                    "_json": "",
                }
        elif event_type == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "thinking_delta":
                thinking_parts.append(delta.get("thinking") or "")
            elif delta.get("type") == "text_delta":
                text_parts.append(delta.get("text") or "")
            elif delta.get("type") == "input_json_delta" and current_tool is not None:
                current_tool["_json"] = str(current_tool.get("_json") or "") + (delta.get("partial_json") or "")
        elif event_type == "content_block_stop" and current_tool is not None:
            raw_json = current_tool.pop("_json", "")
            if raw_json:
                try:
                    current_tool["input"] = json.loads(raw_json)
                except json.JSONDecodeError:
                    current_tool["input"] = {"raw": raw_json}
            tool_blocks.append(current_tool)
            current_tool = None
        elif event_type == "message_delta":
            delta = event.get("delta") or {}
            if delta.get("stop_reason"):
                stop_reason = delta["stop_reason"]
            if event.get("usage"):
                usage = event["usage"]
    content: list[dict[str, Any]] = []
    thinking = "".join(thinking_parts)
    if thinking:
        content.append({"type": "thinking", "thinking": thinking})
    text = "".join(text_parts)
    if text:
        content.append({"type": "text", "text": text})
    content.extend(tool_blocks)
    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model,
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": usage.get("input_tokens") or 0,
            "output_tokens": usage.get("output_tokens") or 0,
        },
    }


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
    request_headers: dict[str, str] | None = None,
    reasoning_map: dict[str, str] | None = None,
) -> RequestLog:
    prompt, completion, total = usage
    now = utcnow()
    session_key = extract_session_key(request_body if isinstance(request_body, dict) else None, request_headers)
    existing = find_continuation_log(
        db,
        account_id=account_id,
        api_key_id=api_key_id,
        protocol=protocol,
        session_key=session_key,
    )
    inbound = extract_request_messages(request_body) if isinstance(request_body, dict) else []
    assistant = extract_assistant_message(protocol, response_body)
    if existing is not None:
        existing.model = model
        existing.stream = stream
        existing.status = status
        existing.http_status = http_status
        existing.error_message = error_message
        existing.prompt_tokens, existing.completion_tokens, existing.total_tokens = accumulate_usage(
            (existing.prompt_tokens, existing.completion_tokens, existing.total_tokens),
            (prompt, completion, total),
        )
        existing.latency_ms = (existing.latency_ms or 0) + latency_ms
        existing.updated_at = now
        if session_key:
            existing.session_key = session_key
        if reasoning_map:
            existing.reasoning_json = dump_reasoning_json(
                merge_reasoning_maps(parse_reasoning_json(existing.reasoning_json), reasoning_map)
            )
        head = load_log_messages_head(db, existing.id, len(inbound))
        tail = load_log_messages_tail(db, existing.id, 1)
        append_log_messages(db, existing.id, new_messages_to_store(head, tail, inbound, assistant))
        db.flush()
        return existing
    stored_key = db.get(ApiKey, api_key_id)
    stored_account = db.get(UpstreamAccount, account_id)
    log = RequestLog(
        account_id=account_id,
        account_name=stored_account.name if stored_account is not None else None,
        account_source=stored_account.source if stored_account is not None else "upstream",
        api_key_id=api_key_id,
        api_key_name=stored_key.name if stored_key is not None else None,
        protocol=protocol,
        model=model,
        stream=stream,
        status=status,
        http_status=http_status,
        error_message=error_message,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=billed_total(prompt, completion, total),
        latency_ms=latency_ms,
        created_at=now,
        updated_at=now,
        session_key=session_key,
        reasoning_json=dump_reasoning_json(reasoning_map or {}),
    )
    db.add(log)
    db.flush()
    to_store = list(inbound)
    if assistant is not None:
        to_store.append(assistant)
    append_log_messages(db, log.id, to_store)
    db.flush()
    return log


async def handle_chat(
    db: Session,
    api_key: ApiKey,
    account: UpstreamAccount,
    body: dict[str, Any],
    protocol: str,
    request_headers: dict[str, str] | None = None,
) -> JSONResponse | StreamingResponse:
    started = time.perf_counter()
    stream = body.get("stream") is True
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
            latency_ms=elapsed_ms(started),
            request_body=body,
            response_body=None,
            request_headers=request_headers,
        )
        return protocol_error(protocol, status_code, str(error))

    provider = get_provider(account.provider)
    if provider.can_passthrough(protocol):
        return await handle_anthropic_passthrough(
            db,
            api_key,
            account,
            body,
            credential,
            started,
            request_headers,
        )

    if protocol == "anthropic_messages":
        messages = anthropic_to_openai_messages(body)
        extra = anthropic_extra_to_openai(body)
    elif protocol == "openai_responses":
        messages = input_to_messages(body)
        extra = responses_extra_passthrough(body)
    else:
        messages = [dict(item) if isinstance(item, dict) else item for item in (body.get("messages") or [])]
        extra = _passthrough_keys(body)

    extra.pop("stream", None)
    extra.pop("thinking", None)
    if stream and protocol != "openai_responses":
        extra = ensure_stream_usage(extra)
    session_key = extract_session_key(body, request_headers)
    inbound_reasoning = reasoning_map_from_messages(messages)
    stored_reasoning = merge_reasoning_maps(
        load_reasoning_map(db, api_key.id, account.id, session_key),
        inbound_reasoning,
    )
    inject_reasoning_into_messages(messages, stored_reasoning)

    if stream:
        return await _stream_response(
            db,
            api_key,
            account,
            body,
            protocol,
            model,
            messages,
            extra,
            credential,
            started,
            request_headers,
            inbound_reasoning,
            responses_input=sanitize_responses_input(body.get("input")),
        )

    try:
        if protocol == "openai_responses":
            result = await call_responses(
                account, sanitize_responses_input(body.get("input")), model, False, extra, credential
            )
        else:
            result = await call_chat(account, messages, model, False, extra, credential)
    except Exception as error:
        return _fail(db, api_key, account, body, protocol, model, stream, started, error, request_headers)

    if hasattr(result, "model_dump"):
        openai_payload = result.model_dump()
    else:
        openai_payload = result if isinstance(result, dict) else {}

    if protocol == "anthropic_messages":
        payload = openai_response_to_anthropic(openai_payload, model)
    elif protocol == "openai_responses":
        payload = responses_payload_to_dict(result)
    else:
        payload = openai_payload

    usage = extract_usage(openai_payload)
    if usage[0] is None and usage[1] is None and usage[2] is None:
        usage = await estimate_usage(model, messages, _output_text_from_openai(openai_payload))
    reasoning_map = (
        reasoning_map_from_responses_payload(payload)
        if protocol == "openai_responses"
        else reasoning_map_from_openai_payload(openai_payload)
    )
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
        latency_ms=elapsed_ms(started),
        request_body=body,
        response_body=payload,
        request_headers=request_headers,
        reasoning_map=merge_reasoning_maps(inbound_reasoning, reasoning_map),
    )
    api_key.last_used_at = utcnow()
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
    request_headers: dict[str, str] | None = None,
    inbound_reasoning: dict[str, str] | None = None,
    responses_input: Any = None,
) -> StreamingResponse | JSONResponse:
    try:
        if protocol == "openai_responses":
            result = await call_responses(account, responses_input, model, True, extra, credential)
        else:
            result = await call_chat(account, messages, model, True, extra, credential)
    except Exception as error:
        return _fail(db, api_key, account, body, protocol, model, True, started, error, request_headers)

    message_id = f"msg_{uuid.uuid4().hex}"
    account_id = account.id
    api_key_id = api_key.id

    async def event_source() -> AsyncIterator[bytes]:
        collector = OpenAIStreamCollector()
        responses_collector = (
            ResponsesStreamCollector(f"resp_{uuid.uuid4().hex}", model)
            if protocol == "openai_responses"
            else None
        )
        translator = AnthropicStreamTranslator(message_id, model)
        usage = (None, None, None)
        error_text: str | None = None
        status = "success"
        http_status = 200
        response_body: Any = None
        response_map: dict[str, str] = {}
        try:
            if protocol == "anthropic_messages":
                for event in translator.start():
                    yield event
            if protocol == "openai_responses":
                # litellm 流自带 response.created 等官方事件，直接透传
                async for event in result:
                    data = responses_event_to_dict(event)
                    responses_collector.feed(data)
                    yield responses_sse(data)
            else:
                async for chunk in stream_openai_chunks(result):
                    usage_candidate = pick_usage_from_chunk(chunk)
                    if usage_candidate is not None:
                        usage = usage_candidate
                    collector.feed(chunk)
                    if protocol == "anthropic_messages":
                        for event in translator.feed(chunk):
                            yield event
                    else:
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
            if protocol == "anthropic_messages":
                for event in translator.finish():
                    yield event
                response_body = translator.payload()
                response_map = translator.reasoning_map()
                usage = translator.usage
            elif responses_collector is not None:
                response_body = responses_collector.payload()
                response_map = responses_collector.reasoning_map()
                usage = responses_collector.usage
            else:
                yield b"data: [DONE]\n\n"
                response_body = {"choices": [{"message": collector.message()}]}
                response_map = collector.reasoning_map()
            if usage[0] is None and usage[1] is None and usage[2] is None:
                if responses_collector is not None:
                    usage = await estimate_usage(model, messages, responses_collector.text)
                else:
                    usage = await estimate_usage(model, messages, _stream_output_text(protocol, collector, translator))
        except Exception as error:
            status = "error"
            http_status = 502
            error_text = str(error)
            if protocol == "anthropic_messages":
                yield sse_event(
                    "error",
                    {"type": "error", "error": {"type": "api_error", "message": error_text}},
                )
            elif responses_collector is not None:
                yield responses_sse(
                    {
                        "type": "response.failed",
                        "response": {
                            "id": responses_collector.response_id,
                            "object": "response",
                            "status": "failed",
                            "error": {"code": "server_error", "message": error_text},
                            "model": model,
                        },
                    }
                )
            else:
                yield f"data: {json.dumps({'error': {'message': error_text}}, ensure_ascii=False)}\n\n".encode()
        finally:
            await asyncio.to_thread(
                _finalize_stream_log,
                account_id=account_id,
                api_key_id=api_key_id,
                protocol=protocol,
                model=model,
                status=status,
                http_status=http_status,
                error_text=error_text,
                usage=usage,
                started=started,
                request_body=body,
                response_body=response_body,
                request_headers=request_headers,
                reasoning_map=merge_reasoning_maps(inbound_reasoning or {}, response_map),
            )

    return StreamingResponse(event_source(), media_type="text/event-stream")


class OpenAIStreamCollector:
    def __init__(self) -> None:
        self.text = ""
        self.reasoning = ""
        self.tools: dict[int, dict[str, Any]] = {}

    def feed(self, chunk: dict[str, Any]) -> None:
        self.text += extract_text_from_openai_chunk(chunk)
        self.reasoning += extract_reasoning_from_openai_chunk(chunk)
        choices = chunk.get("choices") or []
        if not choices:
            return
        delta = (choices[0] or {}).get("delta") or {}
        for tool in delta.get("tool_calls") or []:
            if not isinstance(tool, dict):
                continue
            index = int(tool.get("index") or 0)
            record = self.tools.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            if tool.get("id"):
                record["id"] = tool["id"]
            function = tool.get("function") or {}
            if function.get("name"):
                record["function"]["name"] = function["name"]
            if function.get("arguments"):
                record["function"]["arguments"] += function["arguments"]

    def message(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": "assistant", "content": self.text}
        if self.reasoning:
            payload["reasoning_content"] = self.reasoning
        if self.tools:
            payload["tool_calls"] = [self.tools[index] for index in sorted(self.tools)]
        return payload

    def reasoning_map(self) -> dict[str, str]:
        if not self.reasoning:
            return {}
        return {str(tool["id"]): self.reasoning for tool in self.tools.values() if tool.get("id")}


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
    request_headers: dict[str, str] | None = None,
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
        latency_ms=elapsed_ms(started),
        request_body=body,
        response_body=None,
        request_headers=request_headers,
    )
    return protocol_error(protocol, status_code, message)


async def handle_anthropic_passthrough(
    db: Session,
    api_key: ApiKey,
    account: UpstreamAccount,
    body: dict[str, Any],
    credential: str,
    started: float,
    request_headers: dict[str, str] | None = None,
) -> JSONResponse | StreamingResponse:
    stream = body.get("stream") is True
    model = str(body.get("model") or "")
    inbound_reasoning = reasoning_map_from_anthropic_messages(body.get("messages") if isinstance(body, dict) else None)
    if stream:
        return _stream_anthropic_passthrough(
            api_key,
            account,
            body,
            credential,
            started,
            request_headers,
            inbound_reasoning,
        )
    try:
        status_code, payload = await get_provider(account.provider).post_native(
            account, body, credential, request_headers
        )
    except Exception as error:
        return _fail(db, api_key, account, body, "anthropic_messages", model, False, started, error, request_headers)

    usage = extract_usage(payload if isinstance(payload, dict) else {})
    ok = status_code < 400
    save_log(
        db,
        account_id=account.id,
        api_key_id=api_key.id,
        protocol="anthropic_messages",
        model=model,
        stream=False,
        status="success" if ok else "error",
        http_status=status_code,
        error_message=None if ok else _anthropic_error_message(payload),
        usage=usage,
        latency_ms=elapsed_ms(started),
        request_body=body,
        response_body=payload,
        request_headers=request_headers,
        reasoning_map=merge_reasoning_maps(
            inbound_reasoning,
            reasoning_map_from_anthropic_content(payload.get("content") if isinstance(payload, dict) else None),
        ),
    )
    api_key.last_used_at = utcnow()
    return JSONResponse(payload, status_code=status_code)


def _anthropic_error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if payload.get("message"):
            return str(payload["message"])
    return "上游 Anthropic 请求失败"


def _stream_anthropic_passthrough(
    api_key: ApiKey,
    account: UpstreamAccount,
    body: dict[str, Any],
    credential: str,
    started: float,
    request_headers: dict[str, str] | None,
    inbound_reasoning: dict[str, str],
) -> StreamingResponse:
    provider = get_provider(account.provider)
    url, headers = provider.native_request(account, credential, request_headers)
    timeout = get_settings().request_timeout_seconds
    account_id = account.id
    api_key_id = api_key.id
    model = str(body.get("model") or "")

    async def event_source() -> AsyncIterator[bytes]:
        collected = bytearray()
        status = "success"
        http_status = 200
        error_text: str | None = None
        response_body: Any = None
        usage: tuple[int | None, int | None, int | None] = (None, None, None)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, headers=headers, json=body) as response:
                    http_status = response.status_code
                    if response.status_code >= 400:
                        raw = await response.aread()
                        status = "error"
                        try:
                            payload = json.loads(raw)
                        except json.JSONDecodeError:
                            payload = {
                                "type": "error",
                                "error": {"type": "api_error", "message": raw.decode("utf-8", errors="replace")[:300]},
                            }
                        error_text = _anthropic_error_message(payload)
                        response_body = payload
                        yield sse_event("error", payload if payload.get("type") == "error" else {"type": "error", "error": payload})
                    else:
                        async for chunk in response.aiter_bytes():
                            collected.extend(chunk)
                            yield chunk
                        sse_text = collected.decode("utf-8", errors="replace")
                        response_body = reconstruct_anthropic_from_sse(sse_text, model)
                        usage = extract_usage(response_body)
        except Exception as error:
            status = "error"
            http_status = 504 if "timeout" in str(error).lower() else 502
            error_text = str(error)
            yield sse_event(
                "error",
                {"type": "error", "error": {"type": "api_error", "message": error_text}},
            )
        finally:
            await asyncio.to_thread(
                _finalize_stream_log,
                account_id=account_id,
                api_key_id=api_key_id,
                protocol="anthropic_messages",
                model=model,
                status=status,
                http_status=http_status,
                error_text=error_text,
                usage=usage,
                started=started,
                request_body=body,
                response_body=response_body,
                request_headers=request_headers,
                reasoning_map=merge_reasoning_maps(
                    inbound_reasoning,
                    reasoning_map_from_anthropic_content(
                        response_body.get("content") if isinstance(response_body, dict) else None
                    ),
                ),
            )

    return StreamingResponse(event_source(), media_type="text/event-stream")


async def handle_count_tokens(
    account: UpstreamAccount,
    body: dict[str, Any],
    request_headers: dict[str, str] | None = None,
) -> JSONResponse:
    provider = get_provider(account.provider)
    try:
        native = await provider.count_tokens_native(account, body, request_headers)
    except Exception as error:
        return protocol_error("anthropic_messages", 502, str(error))
    if native is not None:
        status_code, payload = native
        return JSONResponse(payload, status_code=status_code)
    messages = anthropic_to_openai_messages(body)
    model = str(body.get("model") or "claude")
    tokens = await count_openai_tokens(model, messages)
    return JSONResponse({"input_tokens": tokens})


def list_models_payload(api_key: ApiKey) -> dict[str, Any]:
    catalog = build_model_catalog(api_key)
    created = int(time.time())
    data: list[dict[str, Any]] = []
    for entry in catalog:
        item: dict[str, Any] = {
            "id": entry.public_id,
            "object": "model",
            "created": created,
            "owned_by": entry.account.provider,
        }
        if entry.record is not None:
            caps = serialize_record(entry.record)
            item.update(
                {
                    "context_window": caps["context_window"],
                    "max_output_tokens": caps["max_output_tokens"],
                    "reasoning": caps["reasoning"],
                    "reasoning_efforts": caps["reasoning_efforts"],
                    "modalities": caps["modalities"],
                }
            )
        data.append(item)
    return {"object": "list", "data": data}

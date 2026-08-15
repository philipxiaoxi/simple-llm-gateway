from __future__ import annotations

import json
import re
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
from app.services.ccswitch import parse_models_json
from app.services.bridge import (
    AnthropicStreamTranslator,
    anthropic_extra_to_openai,
    anthropic_to_openai_messages,
    call_chat,
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
    responses_from_chat,
    sse_event,
    stream_openai_chunks,
    _passthrough_keys,
)
from app.services.conversation import extract_session_key, find_continuation_log
from app.services.credentials import CredentialError
from app.services.reasoning import (
    dump_reasoning_json,
    inject_reasoning_into_messages,
    load_reasoning_map,
    merge_reasoning_maps,
    parse_reasoning_json,
    reasoning_map_from_messages,
    reasoning_map_from_openai_payload,
)


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


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
    now = datetime.utcnow()
    existing = None
    session_key = extract_session_key(request_body if isinstance(request_body, dict) else None, request_headers)
    if isinstance(request_body, dict):
        existing = find_continuation_log(
            db,
            api_key_id=api_key_id,
            protocol=protocol,
            request_body=request_body,
            session_key=session_key,
        )
    if existing is not None:
        existing.model = model
        existing.stream = stream
        existing.status = status
        existing.http_status = http_status
        existing.error_message = error_message
        if prompt is not None:
            existing.prompt_tokens = prompt
        if completion is not None:
            existing.completion_tokens = (existing.completion_tokens or 0) + completion
        stored_prompt = existing.prompt_tokens
        stored_completion = existing.completion_tokens
        if stored_prompt is not None or stored_completion is not None:
            existing.total_tokens = (stored_prompt or 0) + (stored_completion or 0)
        elif total is not None:
            existing.total_tokens = total
        existing.latency_ms = (existing.latency_ms or 0) + latency_ms
        existing.request_body = dump_json(request_body)
        existing.response_body = dump_json(response_body) if response_body is not None else existing.response_body
        existing.updated_at = now
        if session_key:
            existing.session_key = session_key
        if reasoning_map:
            existing.reasoning_json = dump_reasoning_json(
                merge_reasoning_maps(parse_reasoning_json(existing.reasoning_json), reasoning_map)
            )
        db.flush()
        return existing
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
        total_tokens=(
            total
            if total is not None
            else ((prompt or 0) + (completion or 0) if prompt is not None or completion is not None else None)
        ),
        latency_ms=latency_ms,
        request_body=dump_json(request_body) if request_body is not None else None,
        response_body=dump_json(response_body) if response_body is not None else None,
        created_at=now,
        updated_at=now,
        session_key=session_key,
        reasoning_json=dump_reasoning_json(reasoning_map or {}),
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
    request_headers: dict[str, str] | None = None,
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
            request_headers=request_headers,
        )
        return protocol_error(protocol, status_code, str(error))

    if protocol == "anthropic_messages":
        messages = anthropic_to_openai_messages(body)
        extra = anthropic_extra_to_openai(body)
    elif protocol == "openai_responses":
        messages = input_to_messages(body)
        extra = _passthrough_keys(body)
    else:
        messages = [dict(item) if isinstance(item, dict) else item for item in (body.get("messages") or [])]
        extra = _passthrough_keys(body)

    extra.pop("stream", None)
    extra.pop("thinking", None)
    if stream:
        extra = ensure_stream_usage(extra)
    session_key = extract_session_key(body, request_headers)
    inbound_reasoning = reasoning_map_from_messages(messages)
    stored_reasoning = merge_reasoning_maps(
        load_reasoning_map(db, api_key.id, session_key),
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
        )

    try:
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
        payload = responses_from_chat(openai_payload, model)
    else:
        payload = openai_payload

    usage = extract_usage(openai_payload)
    if usage[0] is None and usage[1] is None and usage[2] is None:
        usage = await estimate_usage(model, messages, _output_text_from_openai(openai_payload))
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
        request_headers=request_headers,
        reasoning_map=merge_reasoning_maps(inbound_reasoning, reasoning_map_from_openai_payload(openai_payload)),
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
    request_headers: dict[str, str] | None = None,
    inbound_reasoning: dict[str, str] | None = None,
) -> StreamingResponse | JSONResponse:
    try:
        result = await call_chat(account, messages, model, True, extra, credential)
    except Exception as error:
        return _fail(db, api_key, account, body, protocol, model, True, started, error, request_headers)

    message_id = f"msg_{uuid.uuid4().hex}"
    account_id = account.id
    api_key_id = api_key.id

    async def event_source() -> AsyncIterator[bytes]:
        collector = OpenAIStreamCollector()
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
            async for chunk in stream_openai_chunks(result):
                usage_candidate = pick_usage_from_chunk(chunk)
                if usage_candidate is not None:
                    usage = usage_candidate
                collector.feed(chunk)
                if protocol == "anthropic_messages":
                    for event in translator.feed(chunk):
                        yield event
                elif protocol == "openai_responses":
                    text = extract_text_from_openai_chunk(chunk)
                    if text:
                        event = {
                            "type": "response.output_text.delta",
                            "delta": text,
                        }
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
                else:
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
            if protocol == "anthropic_messages":
                for event in translator.finish():
                    yield event
                response_body = translator.payload()
                response_map = translator.reasoning_map()
                usage = translator.usage
            elif protocol == "openai_responses":
                completed = {
                    "type": "response.completed",
                    "response": {
                        "id": f"resp_{uuid.uuid4().hex}",
                        "status": "completed",
                        "model": model,
                        "output_text": collector.text,
                    },
                }
                yield f"data: {json.dumps(completed, ensure_ascii=False)}\n\n".encode()
                response_body = completed["response"]
            else:
                yield b"data: [DONE]\n\n"
                response_body = {"choices": [{"message": collector.message()}]}
                response_map = collector.reasoning_map()
            if usage[0] is None and usage[1] is None and usage[2] is None:
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
                    request_headers=request_headers,
                    reasoning_map=merge_reasoning_maps(inbound_reasoning or {}, response_map),
                )
                stored_key = session.get(ApiKey, api_key_id)
                if stored_key is not None:
                    stored_key.last_used_at = datetime.utcnow()
                session.commit()
            finally:
                session.close()

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
        latency_ms=int((time.perf_counter() - started) * 1000),
        request_body=body,
        response_body=None,
        request_headers=request_headers,
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
    models = parse_models_json(account.models_json)
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

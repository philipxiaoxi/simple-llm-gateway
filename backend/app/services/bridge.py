from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import litellm

from app.config import get_settings
from app.models import UpstreamAccount
from app.providers import openai_api_base
from app.services.credentials import require_upstream_credential


def _passthrough_keys(body: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    for key in (
        "temperature",
        "top_p",
        "stop",
        "tools",
        "tool_choice",
        "response_format",
        "max_tokens",
        "max_completion_tokens",
        "user",
        "n",
        "presence_penalty",
        "frequency_penalty",
        "reasoning_effort",
    ):
        if key in body and body[key] is not None:
            extra[key] = body[key]
    return extra


def anthropic_to_openai_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system = body.get("system")
    if system:
        if isinstance(system, list):
            text = "".join(part.get("text", "") for part in system if isinstance(part, dict))
        else:
            text = str(system)
        if text:
            messages.append({"role": "system", "content": text})
    for item in body.get("messages") or []:
        content = item.get("content")
        role = item.get("role") or "user"
        if isinstance(content, list):
            parts: list[dict[str, Any]] = []
            tool_calls = []
            for part in content:
                part_type = part.get("type")
                if part_type == "text":
                    parts.append({"type": "text", "text": part.get("text", "")})
                elif part_type == "image":
                    source = part.get("source") or {}
                    if source.get("type") == "url":
                        parts.append(
                            {"type": "image_url", "image_url": {"url": source.get("url")}}
                        )
                    elif source.get("data"):
                        media = source.get("media_type") or "image/png"
                        parts.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{media};base64,{source['data']}"},
                            }
                        )
                elif part_type == "tool_use":
                    tool_calls.append(
                        {
                            "id": part.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": part.get("name"),
                                "arguments": json.dumps(part.get("input") or {}, ensure_ascii=False),
                            },
                        }
                    )
                elif part_type == "tool_result":
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": part.get("tool_use_id"),
                            "content": part.get("content")
                            if isinstance(part.get("content"), str)
                            else json.dumps(part.get("content"), ensure_ascii=False),
                        }
                    )
            message: dict[str, Any] = {"role": "assistant" if tool_calls else role}
            if parts:
                message["content"] = parts if role != "assistant" or not tool_calls else parts
            elif not tool_calls:
                message["content"] = ""
            if tool_calls:
                message["role"] = "assistant"
                message["tool_calls"] = tool_calls
                if "content" not in message:
                    message["content"] = None
            if message.get("role") == "tool":
                continue
            if parts or tool_calls or message.get("content") is not None:
                messages.append(message)
        else:
            messages.append({"role": role, "content": content})
    return messages


def openai_response_to_anthropic(response: Any, model: str) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        data = response.model_dump()
    elif isinstance(response, dict):
        data = response
    else:
        data = json.loads(response.model_dump_json())
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content_blocks: list[dict[str, Any]] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        content_blocks.append({"type": "text", "text": text})
    elif isinstance(text, list):
        for part in text:
            if isinstance(part, dict) and part.get("type") == "text":
                content_blocks.append({"type": "text", "text": part.get("text", "")})
    for tool in message.get("tool_calls") or []:
        arguments = tool.get("function", {}).get("arguments") or "{}"
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            parsed = {"raw": arguments}
        content_blocks.append(
            {
                "type": "tool_use",
                "id": tool.get("id"),
                "name": tool.get("function", {}).get("name"),
                "input": parsed,
            }
        )
    if not content_blocks:
        content_blocks = [{"type": "text", "text": ""}]
    finish = choice.get("finish_reason")
    stop_reason = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "end_turn",
    }.get(finish, "end_turn")
    usage = data.get("usage") or {}
    return {
        "id": data.get("id") or f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": data.get("model") or model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens") or 0,
            "output_tokens": usage.get("completion_tokens") or 0,
        },
    }


def responses_from_chat(chat: Any, model: str) -> dict[str, Any]:
    if hasattr(chat, "model_dump"):
        data = chat.model_dump()
    elif isinstance(chat, dict):
        data = chat
    else:
        data = {}
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content") or ""
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    usage = data.get("usage") or {}
    return {
        "id": data.get("id") or f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": data.get("model") or model,
        "output": [
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex[:8]}",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": {
            "input_tokens": usage.get("prompt_tokens") or 0,
            "output_tokens": usage.get("completion_tokens") or 0,
            "total_tokens": usage.get("total_tokens") or 0,
        },
    }


def input_to_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    if body.get("messages"):
        return body["messages"]
    raw_input = body.get("input")
    if isinstance(raw_input, str):
        return [{"role": "user", "content": raw_input}]
    if isinstance(raw_input, list):
        messages: list[dict[str, Any]] = []
        for item in raw_input:
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
            elif isinstance(item, dict):
                role = item.get("role") or "user"
                content = item.get("content") or item.get("text") or ""
                messages.append({"role": role, "content": content})
        return messages
    return []


async def call_chat(
    account: UpstreamAccount,
    messages: list[dict[str, Any]],
    model: str,
    stream: bool,
    extra: dict[str, Any],
    api_key: str,
) -> Any:
    settings = get_settings()
    api_base = openai_api_base(account.provider, account.base_url)
    return await litellm.acompletion(
        model=f"openai/{model}",
        messages=messages,
        api_key=api_key,
        api_base=api_base,
        stream=stream,
        timeout=settings.request_timeout_seconds,
        **extra,
    )


async def count_openai_tokens(model: str, messages: list[dict[str, Any]]) -> int:
    try:
        return int(litellm.token_counter(model=model, messages=messages))
    except Exception:
        total = 0
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                total += max(1, len(content) // 4)
            else:
                total += 8
        return total


async def stream_openai_chunks(stream: Any) -> AsyncIterator[dict[str, Any]]:
    async for chunk in stream:
        if hasattr(chunk, "model_dump"):
            yield chunk.model_dump()
        elif isinstance(chunk, dict):
            yield chunk
        else:
            yield json.loads(chunk.model_dump_json())


def extract_usage(payload: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    usage = payload.get("usage") or {}
    prompt = usage.get("prompt_tokens") or usage.get("input_tokens")
    completion = usage.get("completion_tokens") or usage.get("output_tokens")
    total = usage.get("total_tokens")
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return prompt, completion, total


def extract_text_from_openai_chunk(chunk: dict[str, Any]) -> str:
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    return content if isinstance(content, str) else ""


async def prepare_credential(account: UpstreamAccount, db) -> str:  # type: ignore[no-untyped-def]
    if account.provider == "grok" and account.auth_type == "oauth":
        from app.services.grok_oauth import refresh_if_needed

        return await refresh_if_needed(db, account)
    return require_upstream_credential(account)

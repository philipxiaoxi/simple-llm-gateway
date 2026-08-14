from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RequestLog

PLACEHOLDER_REASONING = " "


def extract_reasoning_text(message: dict[str, Any] | None) -> str | None:
    if not isinstance(message, dict):
        return None
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value
    blocks = message.get("thinking_blocks")
    if isinstance(blocks, list):
        parts = [
            str(block.get("thinking"))
            for block in blocks
            if isinstance(block, dict) and block.get("thinking")
        ]
        if parts:
            return "".join(parts)
    extra = message.get("provider_specific_fields")
    if isinstance(extra, dict):
        for key in ("reasoning_content", "reasoning"):
            value = extra.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def extract_reasoning_from_delta(delta: dict[str, Any] | None) -> str:
    if not isinstance(delta, dict):
        return ""
    for source in (delta, delta.get("provider_specific_fields")):
        if not isinstance(source, dict):
            continue
        for key in ("reasoning_content", "reasoning"):
            value = source.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def tool_call_ids_from_message(message: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for tool in message.get("tool_calls") or []:
        if isinstance(tool, dict) and tool.get("id"):
            ids.append(str(tool["id"]))
    return ids


def reasoning_map_from_openai_message(message: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(message, dict):
        return {}
    text = extract_reasoning_text(message)
    if not text:
        return {}
    ids = tool_call_ids_from_message(message)
    if not ids:
        return {}
    return {tool_id: text for tool_id in ids}


def reasoning_map_from_openai_payload(payload: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    choice = (payload.get("choices") or [{}])[0]
    if not isinstance(choice, dict):
        return {}
    return reasoning_map_from_openai_message(choice.get("message") or {})


def reasoning_map_from_anthropic_content(content: Any) -> dict[str, str]:
    if not isinstance(content, list):
        return {}
    thinking_parts: list[str] = []
    tool_ids: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "thinking" and part.get("thinking"):
            thinking_parts.append(str(part.get("thinking")))
        elif part_type == "tool_use" and part.get("id"):
            tool_ids.append(str(part["id"]))
    if not thinking_parts or not tool_ids:
        return {}
    text = "".join(thinking_parts)
    return {tool_id: text for tool_id in tool_ids}


def reasoning_map_from_messages(messages: list[dict[str, Any]]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for message in messages:
        if isinstance(message, dict):
            merged.update(reasoning_map_from_openai_message(message))
    return merged


def merge_reasoning_maps(base: dict[str, str], extra: dict[str, str]) -> dict[str, str]:
    merged = dict(base)
    merged.update(extra)
    return merged


def parse_reasoning_json(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict) and isinstance(data.get("by_tool_call_id"), dict):
        data = data["by_tool_call_id"]
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, str) and value}


def dump_reasoning_json(mapping: dict[str, str]) -> str | None:
    if not mapping:
        return None
    return json.dumps({"by_tool_call_id": mapping}, ensure_ascii=False)


def reasoning_map_from_log(log: RequestLog) -> dict[str, str]:
    mapped = parse_reasoning_json(log.reasoning_json)
    if mapped:
        return mapped
    if not log.response_body:
        return {}
    try:
        body = json.loads(log.response_body)
    except json.JSONDecodeError:
        return {}
    if not isinstance(body, dict):
        return {}
    mapped = reasoning_map_from_anthropic_content(body.get("content"))
    if mapped:
        return mapped
    return reasoning_map_from_openai_payload(body)


def load_reasoning_map(db: Session, api_key_id: int, session_key: str | None) -> dict[str, str]:
    query = select(RequestLog).where(RequestLog.api_key_id == api_key_id)
    if session_key:
        query = query.where(RequestLog.session_key == session_key)
    logs = list(db.scalars(query.order_by(RequestLog.id.desc()).limit(20)).all())
    merged: dict[str, str] = {}
    for log in reversed(logs):
        merged.update(reasoning_map_from_log(log))
    return merged


def inject_reasoning_into_messages(
    messages: list[dict[str, Any]],
    stored: dict[str, str],
) -> list[dict[str, Any]]:
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        if not message.get("tool_calls"):
            continue
        existing = extract_reasoning_text(message)
        if existing:
            message["reasoning_content"] = existing
            continue
        found = None
        for tool_id in tool_call_ids_from_message(message):
            if stored.get(tool_id):
                found = stored[tool_id]
                break
        message["reasoning_content"] = found if found else PLACEHOLDER_REASONING
    return messages

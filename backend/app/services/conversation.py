from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RequestLog

SESSION_HEADER_NAMES = (
    "x-session-id",
    "x-session-affinity",
    "x-parent-session-id",
    "helicone-session-id",
    "x-cursor-conversation-id",
    "x-codex-thread-id",
    "thread-id",
)


def extract_session_key(body: dict[str, Any] | None, headers: dict[str, str] | None = None) -> str | None:
    if isinstance(body, dict):
        for field_name in ("session_id", "conversation_id", "thread_id"):
            value = body.get(field_name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        metadata = body.get("metadata")
        if isinstance(metadata, dict):
            for field_name in ("session_id", "conversation_id"):
                value = metadata.get(field_name)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            user_id = metadata.get("user_id")
            if isinstance(user_id, str) and user_id.strip():
                try:
                    parsed = json.loads(user_id)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    session_id = parsed.get("session_id")
                    if isinstance(session_id, str) and session_id.strip():
                        return session_id.strip()
    if headers:
        lowered = {key.lower(): value for key, value in headers.items()}
        for header_name in SESSION_HEADER_NAMES:
            value = lowered.get(header_name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _content_key(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                part_type = part.get("type")
                if part_type == "tool_use":
                    parts.append(f"tool_use:{part.get('name')}:{part.get('id')}")
                elif part_type == "tool_result":
                    parts.append(f"tool_result:{part.get('tool_use_id')}")
                elif "text" in part:
                    parts.append(str(part.get("text") or ""))
                else:
                    parts.append(json.dumps(part, sort_keys=True, ensure_ascii=False))
            else:
                parts.append(str(part))
        return "\n".join(parts)
    return json.dumps(content, sort_keys=True, ensure_ascii=False)


def normalize_messages(protocol: str, body: dict[str, Any]) -> list[tuple[str, str]]:
    messages = body.get("messages")
    if not isinstance(messages, list):
        if protocol == "openai_responses" and isinstance(body.get("input"), str):
            return [("user", body["input"])]
        return []
    normalized: list[tuple[str, str]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if not role:
            continue
        normalized.append((role, _content_key(item.get("content"))))
    return normalized


def is_continuation(previous_request: dict[str, Any], new_request: dict[str, Any], protocol: str) -> bool:
    previous = normalize_messages(protocol, previous_request)
    current = normalize_messages(protocol, new_request)
    if not previous or not current:
        return False
    if len(current) <= len(previous):
        return False
    return current[: len(previous)] == previous


def extract_log_messages(protocol: str, request_body: Any, response_body: Any) -> list[dict[str, Any]]:
    request = request_body if isinstance(request_body, dict) else {}
    response = response_body if isinstance(response_body, dict) else {}
    messages: list[dict[str, Any]] = []
    raw_messages = request.get("messages")
    if isinstance(raw_messages, list):
        for item in raw_messages:
            if isinstance(item, dict) and item.get("role"):
                messages.append({"role": str(item["role"]), "content": item.get("content")})
    elif isinstance(request.get("input"), str):
        messages.append({"role": "user", "content": request["input"]})
    if protocol == "anthropic_messages":
        content = response.get("content")
        if content:
            messages.append({"role": "assistant", "content": content})
        elif isinstance(response.get("raw_sse"), str):
            texts: list[str] = []
            for match in re.finditer(r'"text_delta"[^}]*"text":\s*"((?:\\.|[^"\\])*)"', response["raw_sse"]):
                try:
                    texts.append(json.loads(f'"{match.group(1)}"'))
                except json.JSONDecodeError:
                    texts.append(match.group(1))
            messages.append({"role": "assistant", "content": "".join(texts)})
        else:
            messages.append({"role": "assistant", "content": ""})
    elif protocol == "openai_responses":
        messages.append({"role": "assistant", "content": response.get("output_text") or response.get("output")})
    else:
        choices = response.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        if isinstance(message, dict):
            messages.append({"role": "assistant", "content": message.get("content")})
    return messages


def find_continuation_log(
    db: Session,
    *,
    api_key_id: int,
    protocol: str,
    request_body: dict[str, Any],
    session_key: str | None = None,
) -> RequestLog | None:
    if session_key:
        return db.scalar(
            select(RequestLog)
            .where(RequestLog.api_key_id == api_key_id, RequestLog.session_key == session_key)
            .order_by(RequestLog.id.desc())
            .limit(1)
        )
    candidates = db.scalars(
        select(RequestLog)
        .where(RequestLog.api_key_id == api_key_id, RequestLog.protocol == protocol)
        .order_by(RequestLog.id.desc())
        .limit(20)
    ).all()
    for log in candidates:
        if not log.request_body:
            continue
        try:
            previous = json.loads(log.request_body)
        except json.JSONDecodeError:
            continue
        if isinstance(previous, dict) and is_continuation(previous, request_body, protocol):
            return log
    return None

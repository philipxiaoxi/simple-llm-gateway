from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models import RequestLog, RequestLogMessage

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


def extract_request_messages(request_body: Any) -> list[dict[str, Any]]:
    request = request_body if isinstance(request_body, dict) else {}
    raw_messages = request.get("messages")
    messages: list[dict[str, Any]] = []
    if isinstance(raw_messages, list):
        for item in raw_messages:
            if not isinstance(item, dict) or not item.get("role"):
                continue
            entry: dict[str, Any] = {"role": str(item["role"]), "content": item.get("content")}
            if item.get("tool_calls"):
                entry["tool_calls"] = item["tool_calls"]
            messages.append(entry)
    elif isinstance(request.get("input"), str):
        messages.append({"role": "user", "content": request["input"]})
    elif isinstance(request.get("input"), list):
        from app.services.bridge import input_to_messages

        for item in input_to_messages(request):
            if not isinstance(item, dict) or not item.get("role"):
                continue
            entry: dict[str, Any] = {"role": str(item["role"]), "content": item.get("content")}
            if item.get("tool_calls"):
                entry["tool_calls"] = item["tool_calls"]
            messages.append(entry)
    return messages


def extract_assistant_message(protocol: str, response_body: Any) -> dict[str, Any] | None:
    response = response_body if isinstance(response_body, dict) else {}
    if protocol == "anthropic_messages":
        content = response.get("content")
        if content:
            return {"role": "assistant", "content": content}
        if isinstance(response.get("raw_sse"), str):
            texts: list[str] = []
            for match in re.finditer(r'"text_delta"[^}]*"text":\s*"((?:\\.|[^"\\])*)"', response["raw_sse"]):
                try:
                    texts.append(json.loads(f'"{match.group(1)}"'))
                except json.JSONDecodeError:
                    texts.append(match.group(1))
            return {"role": "assistant", "content": "".join(texts)}
        return None
    if protocol == "openai_responses":
        output = response.get("output")
        if isinstance(output, list):
            texts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if item_type == "message":
                    content = item.get("content")
                    if isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "output_text":
                                text = part.get("text")
                                if isinstance(text, str):
                                    texts.append(text)
                    elif isinstance(content, str):
                        texts.append(content)
                elif item_type == "function_call":
                    tool_calls.append(
                        {
                            "id": item.get("call_id") or item.get("id"),
                            "type": "function",
                            "function": {
                                "name": item.get("name") or "",
                                "arguments": item.get("arguments") or "{}",
                            },
                        }
                    )
            entry: dict[str, Any] = {"role": "assistant", "content": "".join(texts)}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            return entry
        output_text = response.get("output_text")
        if output_text is None:
            return None
        return {"role": "assistant", "content": output_text}
    choices = response.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        return None
    entry: dict[str, Any] = {"role": "assistant", "content": message.get("content")}
    if message.get("tool_calls"):
        entry["tool_calls"] = message["tool_calls"]
    return entry


def message_fingerprint(message: dict[str, Any]) -> tuple[str, str]:
    extra = ""
    if message.get("tool_calls"):
        extra = json.dumps(message["tool_calls"], sort_keys=True, ensure_ascii=False)
    return (str(message.get("role") or ""), _content_key(message.get("content")) + extra)


def common_prefix_len(stored: list[dict[str, Any]], inbound: list[dict[str, Any]]) -> int:
    length = 0
    for left, right in zip(stored, inbound):
        if message_fingerprint(left) != message_fingerprint(right):
            break
        length += 1
    return length


def new_messages_to_store(
    head: list[dict[str, Any]],
    tail: list[dict[str, Any]],
    inbound: list[dict[str, Any]],
    assistant: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """根据已存会话的头部/尾部片段计算需新增的消息。

    head 为已存会话的前 len(inbound) 条（前缀匹配用），tail 为最后一条（判重用），
    等价于原来的全量 stored，但避免整段会话加载。
    """
    prefix = common_prefix_len(head, inbound)
    added = [dict(item) for item in inbound[prefix:]]
    if assistant is None:
        return added
    if added and message_fingerprint(added[-1]) == message_fingerprint(assistant):
        return added
    last = tail[-1] if tail else None
    if not added and last is not None and message_fingerprint(last) == message_fingerprint(assistant):
        return []
    added.append(dict(assistant))
    return added


def decode_stored_message(row: RequestLogMessage) -> dict[str, Any]:
    content = None
    if row.content_json:
        try:
            content = json.loads(row.content_json)
        except json.JSONDecodeError:
            content = row.content_json
    if isinstance(content, dict) and "content" in content and "role" not in content:
        entry = {"role": row.role, "content": content.get("content")}
        if content.get("tool_calls"):
            entry["tool_calls"] = content["tool_calls"]
        return entry
    return {"role": row.role, "content": content}


def encode_message_content(message: dict[str, Any]) -> str:
    payload: dict[str, Any] = {"content": message.get("content")}
    if message.get("tool_calls"):
        payload["tool_calls"] = message["tool_calls"]
    return json.dumps(payload, ensure_ascii=False, default=str)


def load_log_messages_head(db: Session, log_id: int, limit: int) -> list[dict[str, Any]]:
    """按 seq 升序加载会话前 limit 条消息，用于增量前缀匹配。"""
    rows = db.scalars(
        select(RequestLogMessage)
        .where(RequestLogMessage.log_id == log_id)
        .order_by(RequestLogMessage.seq.asc())
        .limit(limit)
    ).all()
    return [decode_stored_message(row) for row in rows]


def load_log_messages_tail(db: Session, log_id: int, limit: int) -> list[dict[str, Any]]:
    """按 seq 升序加载会话最后 limit 条消息，用于尾部判重。"""
    rows = db.scalars(
        select(RequestLogMessage)
        .where(RequestLogMessage.log_id == log_id)
        .order_by(RequestLogMessage.seq.desc())
        .limit(limit)
    ).all()
    return [decode_stored_message(row) for row in reversed(rows)]


def append_log_messages(db: Session, log_id: int, messages: list[dict[str, Any]]) -> None:
    if not messages:
        return
    last_seq = db.scalar(select(func.max(RequestLogMessage.seq)).where(RequestLogMessage.log_id == log_id))
    next_seq = (last_seq if last_seq is not None else -1) + 1
    now = utcnow()
    for offset, message in enumerate(messages):
        db.add(
            RequestLogMessage(
                log_id=log_id,
                seq=next_seq + offset,
                role=str(message.get("role") or "user"),
                content_json=encode_message_content(message),
                created_at=now,
            )
        )


def find_continuation_log(
    db: Session,
    *,
    account_id: int,
    api_key_id: int,
    protocol: str,
    session_key: str | None = None,
) -> RequestLog | None:
    # 无 session 时不猜测会话边界，直接按新会话处理，避免加载最近会话做前缀匹配。
    if not session_key:
        return None
    return db.scalar(
        select(RequestLog)
        .where(
            RequestLog.account_id == account_id,
            RequestLog.api_key_id == api_key_id,
            RequestLog.session_key == session_key,
            RequestLog.protocol == protocol,
        )
        .order_by(RequestLog.id.desc())
        .limit(1)
    )

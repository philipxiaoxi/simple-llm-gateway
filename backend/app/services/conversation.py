from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, load_only

from app.clock import shanghai_day_start_utc, utcnow
from app.models import RequestLog, RequestLogMessage

SESSION_HEADER_NAMES = (
    # 各客户端/上游的会话头，优先识别，便于网关把同一会话的多轮请求合并到一条记录
    "x-opencode-session",
    "x-claude-code-session-id",
    "x-grok-session-id",
    "x-grok-conv-id",
    "x-session-id",
    "x-session-affinity",
    "x-parent-session-id",
    "helicone-session-id",
    "x-cursor-conversation-id",
    "x-codex-thread-id",
    "thread-id",
    "session-id",
    "x-client-request-id",
)

# 单会话最多保留的消息条数（滚动窗口）：保留头部连续系统消息 + 最新 N 条，
# 超过后从最旧的普通消息开始清理，防止超长会话把 request_log_messages 表撑爆
MAX_LOG_MESSAGES = 1000


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


def conversation_fingerprint(body: dict[str, Any] | None) -> str | None:
    """无会话标识时，用对话「开头」的内容指纹近似会话身份。

    取系统提示与第一条用户消息：同一会话的后续轮次保持不变，不同会话通常不同。
    仅用于给上游伪造稳定会话头，不参与网关自身的会话归类。
    """
    messages = extract_request_messages(body)
    if not messages:
        return None
    picked: list[str] = []
    for message in messages:
        role = str(message.get("role") or "")
        if role == "system":
            picked.append("system:" + _content_key(message.get("content")))
        elif role == "user":
            picked.append("user:" + _content_key(message.get("content")))
            break
    if not picked:
        return None
    return hashlib.sha256("\n".join(picked).encode("utf-8")).hexdigest()[:32]


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
    tail: list[dict[str, Any]],
    inbound: list[dict[str, Any]],
    assistant: dict[str, Any] | None,
    head_loader: Callable[[], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """根据已存会话的尾部片段计算需新增的消息。

    热路径只读取最后一条已存消息（tail，limit=1）：
    - inbound 末条 == 已存末条：重发/无新增；
    - inbound 倒数第二条 == 已存末条：正常续写（含滚动窗口/裁剪历史），只补录末条；
    - 其余情况才通过 head_loader 加载头部做前缀匹配兜底（会话分叉、客户端裁剪等罕见场景）。
    """
    total = len(inbound)
    last_stored = tail[-1] if tail else None
    if total == 0:
        added: list[dict[str, Any]] = []
    elif last_stored is not None and message_fingerprint(last_stored) == message_fingerprint(inbound[-1]):
        # 客户端历史已包含最后一条已存消息：重发/无新增
        added = []
    elif (
        last_stored is not None
        and total >= 2
        and message_fingerprint(last_stored) == message_fingerprint(inbound[-2])
    ):
        # 正常续写：尾部已对齐，只补录本次新发的最后一条（滚出/裁剪的中间消息不重复入库）
        added = [dict(inbound[total - 1])]
    else:
        # 兜底：与已存尾部对不齐，回退到头部前缀匹配
        head = head_loader() if head_loader is not None else []
        prefix = common_prefix_len(head, inbound)
        added = [dict(item) for item in inbound[prefix:]]
    if assistant is None:
        return added
    if added and message_fingerprint(added[-1]) == message_fingerprint(assistant):
        return added
    if not added and last_stored is not None and message_fingerprint(last_stored) == message_fingerprint(assistant):
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


PREVIEW_CHAR_LIMIT = 500
TOOL_ARGS_PREVIEW_LIMIT = 200


def content_preview(content: Any) -> tuple[str | None, bool, int]:
    """列表用轻量预览：返回 (预览文本, 是否截断, 全文长度)，避免大正文进列表响应。"""
    text = _content_key(content)
    length = len(text)
    if length <= PREVIEW_CHAR_LIMIT:
        return text or None, False, length
    return text[:PREVIEW_CHAR_LIMIT], True, length


def tool_calls_summary(raw: Any) -> list[dict[str, str]] | None:
    """工具调用摘要：只保留 id/name 与参数预览，列表接口不传完整参数。"""
    if not isinstance(raw, list):
        return None
    items: list[dict[str, str]] = []
    for call in raw:
        if not isinstance(call, dict):
            continue
        fn = call.get("function")
        fn = fn if isinstance(fn, dict) else None
        name = str((fn.get("name") if fn else call.get("name")) or "") or "工具调用"
        args = call.get("arguments")
        if args is None:
            args = (fn.get("arguments") if fn else None) or call.get("input")
        if isinstance(args, dict | list):
            args = json.dumps(args, ensure_ascii=False, default=str)
        args = str(args or "")
        items.append(
            {
                "id": str(call.get("id") or call.get("call_id") or ""),
                "name": name,
                "arguments_preview": args[:TOOL_ARGS_PREVIEW_LIMIT],
            }
        )
    return items or None


def encode_message_content(message: dict[str, Any]) -> str:
    payload: dict[str, Any] = {"content": message.get("content")}
    if message.get("tool_calls"):
        payload["tool_calls"] = message["tool_calls"]
    return json.dumps(payload, ensure_ascii=False, default=str)


_MESSAGE_COMPARE_COLUMNS = (
    RequestLogMessage.seq,
    RequestLogMessage.role,
    RequestLogMessage.content_json,
)


def load_log_messages_head(db: Session, log_id: int, limit: int) -> list[dict[str, Any]]:
    """按 seq 升序加载会话前 limit 条消息，用于增量前缀匹配。"""
    rows = db.scalars(
        select(RequestLogMessage)
        .where(RequestLogMessage.log_id == log_id)
        .order_by(RequestLogMessage.seq.asc())
        .limit(limit)
        .options(load_only(*_MESSAGE_COMPARE_COLUMNS))
    ).all()
    return [decode_stored_message(row) for row in rows]


def load_log_messages_tail(db: Session, log_id: int, limit: int) -> list[dict[str, Any]]:
    """按 seq 升序加载会话最后 limit 条消息，用于尾部判重。"""
    rows = db.scalars(
        select(RequestLogMessage)
        .where(RequestLogMessage.log_id == log_id)
        .order_by(RequestLogMessage.seq.desc())
        .limit(limit)
        .options(load_only(*_MESSAGE_COMPARE_COLUMNS))
    ).all()
    return [decode_stored_message(row) for row in reversed(rows)]


def append_log_messages(db: Session, log_id: int, messages: list[dict[str, Any]]) -> None:
    if not messages:
        return
    # 一次查询拿到 max(seq) 与当前条数：计数用于滚动窗口判断，避免追加后再 COUNT 一次
    last_seq, total_before = db.execute(
        select(func.max(RequestLogMessage.seq), func.count()).where(
            RequestLogMessage.log_id == log_id
        )
    ).one()
    next_seq = (last_seq if last_seq is not None else -1) + 1
    total = (total_before or 0) + len(messages)
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
    if total <= MAX_LOG_MESSAGES:
        # session 为 autoflush=False，这里是本次写入唯一的 flush 点（交给调用方 commit）
        db.flush()
        return
    # 滚动窗口：保留头部连续系统消息 + 最新 MAX_LOG_MESSAGES 条，
    # 超出部分从最旧的普通消息开始清理（seq 持续递增，删除后留下空洞不影响查询）
    db.flush()
    excess = total - MAX_LOG_MESSAGES
    first_non_system = db.scalar(
        select(func.min(RequestLogMessage.seq)).where(
            RequestLogMessage.log_id == log_id,
            RequestLogMessage.role != "system",
        )
    )
    oldest = select(RequestLogMessage.id).where(RequestLogMessage.log_id == log_id)
    if first_non_system is not None:
        oldest = oldest.where(RequestLogMessage.seq >= first_non_system)
    # 单条 DELETE ... WHERE id IN (SELECT ... ORDER BY seq LIMIT excess)，避免逐行加载/删除
    db.execute(
        delete(RequestLogMessage)
        .where(
            RequestLogMessage.id.in_(
                oldest.order_by(RequestLogMessage.seq.asc()).limit(excess)
            )
        )
        .execution_options(synchronize_session=False)
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
    # 续写只在同一自然日（上海时区）内合并：跨天复用同一 session 时另起一条记录。
    # 否则新请求的用量会累加到旧记录上（created_at 停留在旧日期），
    # 导致概览、Key 用量、分享页的「今日请求 / 今日 Token」漏统计。
    day_start = shanghai_day_start_utc()
    return db.scalar(
        select(RequestLog)
        .where(
            RequestLog.account_id == account_id,
            RequestLog.api_key_id == api_key_id,
            RequestLog.session_key == session_key,
            RequestLog.protocol == protocol,
            RequestLog.created_at >= day_start,
        )
        .order_by(RequestLog.id.desc())
        .limit(1)
    )

"""根据最近会话生成「今天想做什么」快捷建议。

历史对话先合并成最多 5000 字的文本，再交给当前助手模型推断最多 6 条建议。
生成结果按「管理员 + 账号 + 模型 + 历史摘要」持久化到数据库：历史不变时直接复用，
重启进程也不会重新生成；历史或模型变化后自动覆盖为新的结果。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.store import list_conversations
from app.clock import utcnow
from app.models import AssistantMessage, AssistantSuggestionCache
from app.services.bridge import call_chat, extract_text_from_openai_chunk, stream_openai_chunks

MAX_HISTORY_CHARS = 5000
MAX_CONVERSATIONS = 8
MAX_MESSAGES_PER_CONVERSATION = 20
MAX_MESSAGE_CHARS = 600
MAX_SUGGESTIONS = 6

DEFAULT_SUGGESTIONS: list[dict[str, str]] = [
    {"label": "打开 MCP 广场", "prompt": "帮我打开 MCP 广场"},
    {"label": "查看最近的日志", "prompt": "帮我看看最近的请求日志"},
    {"label": "下载抖音视频", "prompt": "帮我下载这个抖音视频："},
    {"label": "检索知识库", "prompt": "在知识库里检索："},
]

_SYSTEM_PROMPT = """你是管理后台的 AI 助手。请只根据用户最近的对话历史，推断用户接下来最可能想继续做的事，输出快捷建议。
规则：
1. 最多 6 条，信息不足就少给，不要凑数。
2. label 是 2-10 个汉字的按钮文案；prompt 是点击后直接发送给助手的完整指令，不超过 80 字。
3. 尽量引用历史里的具体对象（某类抖音视频、某个知识库、站点、生成任务等），不要编造不存在的 id 或链接。
4. 不要输出解释、Markdown 或代码块，只输出 JSON：{"items":[{"label":"...","prompt":"..."}]}"""

_LOCKS: dict[tuple[Any, ...], asyncio.Lock] = {}


def build_history_text(db: Session, admin_id: int) -> str:
    """把最近会话合并成最多 5000 字的文本；没有可用内容时返回空串。"""

    conversations = list_conversations(db, admin_id, limit=MAX_CONVERSATIONS)
    blocks: list[str] = []
    for conversation in reversed(conversations):
        rows = db.scalars(
            select(AssistantMessage)
            .where(
                AssistantMessage.conversation_id == conversation["id"],
                AssistantMessage.role.in_(("user", "assistant")),
            )
            .order_by(AssistantMessage.id.desc())
            .limit(MAX_MESSAGES_PER_CONVERSATION)
        ).all()
        lines: list[str] = []
        for row in reversed(rows):
            content = (row.content or "").strip()
            if not content:
                continue
            prefix = "用户：" if row.role == "user" else "助手："
            lines.append(prefix + content[:MAX_MESSAGE_CHARS])
        if lines:
            blocks.append(f"# {conversation['title']}\n" + "\n".join(lines))
    text = "\n\n".join(blocks).strip()
    if len(text) > MAX_HISTORY_CHARS:
        # 优先保留最近的对话内容
        text = text[-MAX_HISTORY_CHARS:]
    return text


def _validate_items(items: Any) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        if not label or not prompt or prompt in seen:
            continue
        seen.add(prompt)
        result.append({"label": label[:20], "prompt": prompt[:200]})
        if len(result) >= MAX_SUGGESTIONS:
            break
    return result


def parse_suggestions(raw: str) -> list[dict[str, str]]:
    """解析模型输出，过滤无效项并限制最多 6 条。"""

    text = (raw or "").strip()
    if not text:
        return []
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except (TypeError, ValueError):
        return []
    return _validate_items(data.get("items") if isinstance(data, dict) else None)


async def _generate_with_llm(runtime: Any, history: str) -> list[dict[str, str]]:
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"历史对话（最多 5000 字）：\n{history}"},
    ]
    extra = {"temperature": 0.4, "max_tokens": 800}
    stream = await call_chat(
        runtime.account,
        messages,
        runtime.model,
        True,
        extra,
        runtime.credential,
    )
    parts: list[str] = []
    size = 0
    async for chunk in stream_openai_chunks(stream):
        if not isinstance(chunk, dict):
            continue
        if chunk.get("error"):
            raise RuntimeError(str(chunk["error"]))
        text = extract_text_from_openai_chunk(chunk)
        if not text:
            continue
        parts.append(text)
        size += len(text)
        if size > 4000:
            break
    return parse_suggestions("".join(parts))


def _lock_for(key: tuple[Any, ...]) -> asyncio.Lock:
    lock = _LOCKS.get(key)
    if lock is None:
        if len(_LOCKS) > 256:
            _LOCKS.clear()
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


def _load_cached(
    db: Session, admin_id: int, account_id: int | None, model: str, digest: str
) -> list[dict[str, str]] | None:
    row = db.get(AssistantSuggestionCache, admin_id)
    if row is None:
        return None
    if row.account_id != account_id or row.model != model or row.history_hash != digest:
        return None
    try:
        items = _validate_items(json.loads(row.items_json or "[]"))
    except (TypeError, ValueError):
        return None
    return items or None


def _store_cache(
    db: Session,
    admin_id: int,
    account_id: int | None,
    model: str,
    digest: str,
    items: list[dict[str, str]],
) -> None:
    row = db.get(AssistantSuggestionCache, admin_id)
    if row is None:
        row = AssistantSuggestionCache(admin_id=admin_id)
        db.add(row)
    row.account_id = account_id
    row.model = model
    row.history_hash = digest
    row.items_json = json.dumps(items, ensure_ascii=False)
    row.updated_at = utcnow()
    db.commit()


async def build_suggestions(
    db: Session, admin_id: int, runtime: Any
) -> tuple[list[dict[str, str]], str]:
    """返回 (建议列表, 来源)。来源为 history 表示由模型按历史生成，default 表示默认建议。"""

    history = build_history_text(db, admin_id)
    if not history or runtime is None:
        return DEFAULT_SUGGESTIONS, "default"
    digest = hashlib.sha256(history.encode("utf-8")).hexdigest()
    account_id = runtime.account.id
    model = runtime.model
    cached = _load_cached(db, admin_id, account_id, model, digest)
    if cached is not None:
        return cached, "history"
    key = (admin_id, account_id, model, digest)
    async with _lock_for(key):
        cached = _load_cached(db, admin_id, account_id, model, digest)
        if cached is not None:
            return cached, "history"
        try:
            items = await _generate_with_llm(runtime, history)
        except Exception:  # noqa: BLE001 - 建议生成失败时退回默认建议
            items = []
        if not items:
            return DEFAULT_SUGGESTIONS, "default"
        _store_cache(db, admin_id, account_id, model, digest, items)
        return items, "history"

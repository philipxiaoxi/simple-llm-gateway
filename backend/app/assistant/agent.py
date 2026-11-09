from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from sqlalchemy.exc import OperationalError

from app.assistant.prompts import build_system_prompt
from app.assistant.store import (
    LEGACY_SYSTEM_PROMPTS,
    append_message,
    conversation_title_from_text,
    get_conversation,
    load_llm_messages,
    settings_row,
    touch_conversation,
)
from app.assistant.tools import build_tool_defs, execute_tool, llm_result
from app.db import get_session_factory
from app.models import Admin, AssistantConversation, UpstreamAccount
from app.services.bridge import (
    call_chat,
    ensure_stream_usage,
    estimate_usage,
    extract_reasoning_from_openai_chunk,
    extract_text_from_openai_chunk,
    extract_usage,
    prepare_credential,
    stream_openai_chunks,
)
from app.services.credentials import CredentialError
from app.services.permission_guard import permissions_for_admin

Event = tuple[str, dict[str, Any]]
DisconnectCheck = Callable[[], Awaitable[bool]]


@dataclass
class Runtime:
    account: Any
    model: str
    credential: str
    system_prompt: str
    temperature: float
    max_tokens: int
    max_tool_rounds: int
    history_message_limit: int


def _persist_message(
    conversation_id: str,
    role: str,
    content: str,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
    meta: dict[str, Any] | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    title_hint: str = "",
) -> tuple[int, str]:
    factory = get_session_factory()
    session = factory()
    try:
        row = append_message(
            session,
            conversation_id,
            role,
            content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            meta=meta,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        conversation = session.get(AssistantConversation, conversation_id)
        if conversation is not None:
            touch_conversation(session, conversation, title=title_hint or None)
        session.commit()
        return row.id, conversation.title if conversation is not None else ""
    finally:
        session.close()


async def _persist_async(*args: Any, **kwargs: Any) -> tuple[int, str]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return await asyncio.to_thread(_persist_message, *args, **kwargs)
        except OperationalError as error:
            last_error = error
            if attempt < 2:
                await asyncio.sleep(0.25 * (attempt + 1))
    raise last_error or RuntimeError("保存消息失败")


def _load_history(conversation_id: str, limit: int) -> list[dict[str, Any]]:
    factory = get_session_factory()
    session = factory()
    try:
        return load_llm_messages(session, conversation_id, limit)
    finally:
        session.close()


async def _prepare_turn(admin_id: int) -> tuple[Runtime | None, set[str], str]:
    factory = get_session_factory()
    session = factory()
    try:
        row = settings_row(session)
        if row is None:
            return None, set(), "助手尚未配置"
        admin = session.get(Admin, admin_id)
        if admin is None:
            return None, set(), "账号不存在"
        permissions = permissions_for_admin(session, admin)
        if not row.enabled:
            return None, permissions, "助手已停用"
        if not row.account_id or not row.model.strip():
            return None, permissions, "尚未选择对话模型"
        if row.system_prompt.strip() in LEGACY_SYSTEM_PROMPTS:
            row.system_prompt = ""
        account = session.get(UpstreamAccount, row.account_id)
        if account is None or account.source != "upstream":
            return None, permissions, "所选上游账号不存在"
        if account.status != "active":
            return None, permissions, "所选上游账号已停用"
        try:
            credential = await prepare_credential(account, session)
        except CredentialError as error:
            return None, permissions, error.message
        session.commit()
        runtime = Runtime(
            account=SimpleNamespace(
                id=account.id,
                name=account.name,
                provider=account.provider,
                base_url=account.base_url,
                source=account.source,
                auth_type=account.auth_type,
                header_spoof=account.header_spoof,
            ),
            model=row.model.strip(),
            credential=credential,
            system_prompt="" if row.system_prompt.strip() in LEGACY_SYSTEM_PROMPTS else row.system_prompt,
            temperature=float(row.temperature),
            max_tokens=int(row.max_tokens),
            max_tool_rounds=int(row.max_tool_rounds),
            history_message_limit=int(row.history_message_limit),
        )
        return runtime, permissions, ""
    finally:
        session.close()


def _accumulate_tool_calls(acc: dict[int, dict[str, Any]], raw_calls: Any) -> None:
    if not isinstance(raw_calls, list):
        return
    for item in raw_calls:
        if not isinstance(item, dict):
            continue
        raw_index = item.get("index")
        try:
            index = int(raw_index) if raw_index is not None else len(acc)
        except (TypeError, ValueError):
            index = len(acc)
        record = acc.setdefault(
            index,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if item.get("id"):
            record["id"] = str(item["id"])
        function = item.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if name:
                record["function"]["name"] = str(name)
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                record["function"]["arguments"] += arguments
            elif isinstance(arguments, dict):
                record["function"]["arguments"] += json.dumps(arguments, ensure_ascii=False)


def _finalize_tool_calls(acc: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for index in sorted(acc):
        record = acc[index]
        function = record.get("function") or {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        call_id = str(record.get("id") or "").strip() or f"call_{uuid.uuid4().hex[:12]}"
        arguments = function.get("arguments") or "{}"
        calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    return calls


def _arguments_dict(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.strip():
        return {}, ""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}, "工具参数不是合法 JSON"
    if not isinstance(value, dict):
        return {}, "工具参数必须是 JSON 对象"
    return value, ""


def _chunk_choices(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    choices = chunk.get("choices")
    return [item for item in choices if isinstance(item, dict)] if isinstance(choices, list) else []


async def run_turn(
    *,
    admin_id: int,
    admin_name: str,
    conversation_id: str,
    user_text: str | None,
    page: dict[str, Any] | None,
    is_disconnected: DisconnectCheck,
    token: str,
    app,
    continuation: bool = False,
    public_origin: str = "",
) -> AsyncIterator[Event]:
    factory = get_session_factory()
    session = factory()
    try:
        conversation = get_conversation(session, admin_id, conversation_id)
    finally:
        session.close()
    if conversation is None:
        yield "error", {"message": "会话不存在或无权访问", "code": "not_found"}
        return

    text = (user_text or "").strip()
    if not continuation and not text:
        yield "error", {"message": "消息不能为空", "code": "empty_message"}
        return

    runtime, permissions, config_error = await _prepare_turn(admin_id)
    if runtime is None:
        yield "error", {"message": config_error or "助手尚未配置", "code": "not_configured"}
        return

    title = conversation.title
    user_message_id: int | None = None
    if not continuation:
        title_hint = conversation_title_from_text(text)
        try:
            user_message_id, title = await _persist_async(
                conversation_id,
                "user",
                text,
                meta={"page": page or {}},
                title_hint=title_hint,
            )
        except Exception as error:  # noqa: BLE001
            yield "error", {"message": f"保存消息失败：{error}", "code": "persist_failed"}
            return

    yield "meta", {
        "conversation_id": conversation_id,
        "user_message_id": user_message_id,
        "title": title,
        "model": runtime.model,
        "continuation": continuation,
    }

    system_prompt = build_system_prompt(
        admin_name=admin_name,
        page=page,
        permissions=permissions,
        custom_prompt=runtime.system_prompt,
    )
    try:
        history = await asyncio.to_thread(_load_history, conversation_id, runtime.history_message_limit)
    except Exception as error:  # noqa: BLE001
        yield "error", {"message": f"读取会话失败：{error}", "code": "history_failed"}
        return
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}, *history]
    tool_defs = build_tool_defs()

    total_prompt_tokens = 0
    total_completion_tokens = 0
    final_saved = False

    for round_index in range(max(1, runtime.max_tool_rounds)):
        if await is_disconnected():
            return
        yield "status", {"stage": "thinking", "round": round_index + 1}
        tool_acc: dict[int, dict[str, Any]] = {}
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        finish_reason = ""
        round_prompt = 0
        round_completion = 0
        try:
            extra: dict[str, Any] = {"temperature": runtime.temperature}
            if runtime.max_tokens > 0:
                extra["max_tokens"] = runtime.max_tokens
            extra["tools"] = tool_defs
            extra["tool_choice"] = "auto"
            extra = ensure_stream_usage(extra)
            stream = await call_chat(
                runtime.account,
                messages,
                runtime.model,
                True,
                extra,
                runtime.credential,
            )
            async for chunk in stream_openai_chunks(stream):
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("error"):
                    raise RuntimeError(str(chunk.get("error")))
                usage = extract_usage(chunk)
                if usage[0] is not None:
                    round_prompt = int(usage[0])
                if usage[1] is not None:
                    round_completion = int(usage[1])
                for choice in _chunk_choices(chunk):
                    delta = choice.get("delta") or {}
                    reasoning = extract_reasoning_from_openai_chunk(chunk)
                    if reasoning and not text_parts:
                        reasoning_parts.append(reasoning)
                        yield "reasoning", {"delta": reasoning}
                    content = extract_text_from_openai_chunk(chunk)
                    if content:
                        if not text_parts:
                            yield "status", {"stage": "writing", "round": round_index + 1}
                        text_parts.append(content)
                        yield "text", {"delta": content}
                    _accumulate_tool_calls(tool_acc, delta.get("tool_calls"))
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])
                if await is_disconnected():
                    partial = "".join(text_parts)
                    if partial or reasoning_parts:
                        await _persist_async(
                            conversation_id,
                            "assistant",
                            partial,
                            meta={"interrupted": True, "reasoning": "".join(reasoning_parts)},
                        )
                    return
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            message = str(error).strip() or type(error).__name__
            await _persist_async(
                conversation_id,
                "assistant",
                "",
                meta={"error": message[:1000]},
            )
            yield "error", {"message": f"模型调用失败：{message[:500]}", "code": "upstream_error"}
            return

        total_prompt_tokens += round_prompt
        total_completion_tokens += round_completion
        assistant_text = "".join(text_parts)
        assistant_reasoning = "".join(reasoning_parts)
        calls = _finalize_tool_calls(tool_acc)

        if calls:
            await _persist_async(
                conversation_id,
                "assistant",
                assistant_text,
                tool_calls=calls,
                meta={"reasoning": assistant_reasoning} if assistant_reasoning else {},
            )
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": assistant_text or None,
                "tool_calls": calls,
            }
            if assistant_reasoning:
                assistant_message["reasoning_content"] = assistant_reasoning
            messages.append(assistant_message)
            awaiting_approval = False
            for call_index, call in enumerate(calls):
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                raw_arguments = str(function.get("arguments") or "{}")
                arguments, argument_error = _arguments_dict(raw_arguments)
                yield "tool_call", {
                    "id": call["id"],
                    "name": name,
                    "arguments": arguments if not argument_error else {},
                    "api_id": arguments.get("api_id") if name == "api_call" else None,
                }
                if argument_error:
                    outcome = {
                        "kind": "result",
                        "ok": False,
                        "result": None,
                        "error": argument_error,
                        "summary": argument_error,
                        "latency_ms": 0,
                        "approval": None,
                        "navigate": None,
                    }
                else:
                    yield "status", {"stage": "tool", "name": name}
                    try:
                        outcome = await execute_tool(
                            app=app,
                            token=token,
                            permissions=permissions,
                            admin_id=admin_id,
                            conversation_id=conversation_id,
                            tool_call_id=call["id"],
                            name=name,
                            arguments=arguments,
                            public_origin=public_origin,
                        )
                    except Exception as error:  # noqa: BLE001
                        outcome = {
                            "kind": "result",
                            "ok": False,
                            "result": None,
                            "error": str(error)[:500],
                            "summary": f"工具执行失败：{error}",
                            "latency_ms": 0,
                            "approval": None,
                            "navigate": None,
                        }
                if outcome.get("kind") == "approval":
                    approval = outcome.get("approval") or {}
                    yield "approval_required", {
                        "tool_call_id": call["id"],
                        "approval": approval,
                    }
                    awaiting_approval = True
                    for skipped in calls[call_index + 1 :]:
                        skipped_function = skipped.get("function") or {}
                        skipped_name = str(skipped_function.get("name") or "")
                        skipped_message = "前一个写操作等待审批，本次调用未执行"
                        skipped_content = json.dumps({"error": skipped_message}, ensure_ascii=False)
                        await _persist_async(
                            conversation_id,
                            "tool",
                            skipped_content,
                            tool_call_id=skipped["id"],
                            tool_name=skipped_name,
                            meta={
                                "ok": False,
                                "result": None,
                                "summary": skipped_message,
                                "error": skipped_message,
                                "latency_ms": 0,
                                "kind": "result",
                                "skipped": True,
                            },
                        )
                        yield "tool_result", {
                            "id": skipped["id"],
                            "name": skipped_name,
                            "ok": False,
                            "summary": skipped_message,
                            "result": None,
                            "error": skipped_message,
                            "latency_ms": 0,
                            "kind": "result",
                            "skipped": True,
                        }
                    break
                yield "tool_result", {
                    "id": call["id"],
                    "name": name,
                    "ok": bool(outcome.get("ok")),
                    "summary": outcome.get("summary") or "",
                    "result": outcome.get("result"),
                    "error": outcome.get("error") or "",
                    "latency_ms": int(outcome.get("latency_ms") or 0),
                    "kind": outcome.get("kind") or "result",
                }
                if outcome.get("navigate"):
                    yield "navigate", {
                        "path": outcome["navigate"],
                        "title": (outcome.get("result") or {}).get("title", ""),
                    }
                tool_content = (
                    llm_result(outcome.get("result"))
                    if outcome.get("ok")
                    else json.dumps({"error": outcome.get("error") or "工具执行失败"}, ensure_ascii=False)
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "name": name,
                        "content": tool_content,
                    }
                )
                await _persist_async(
                    conversation_id,
                    "tool",
                    tool_content,
                    tool_call_id=call["id"],
                    tool_name=name,
                    meta={
                        "ok": bool(outcome.get("ok")),
                        "result": outcome.get("result"),
                        "summary": outcome.get("summary") or "",
                        "error": outcome.get("error") or "",
                        "latency_ms": int(outcome.get("latency_ms") or 0),
                        "kind": outcome.get("kind") or "result",
                    },
                )
            if awaiting_approval:
                yield "done", {
                    "conversation_id": conversation_id,
                    "message_id": None,
                    "title": title,
                    "awaiting_approval": True,
                }
                return
            continue

        assistant_text = assistant_text.strip()
        if not assistant_text:
            assistant_text = "模型没有返回可显示的内容，请换个说法再试。"
            yield "text", {"delta": assistant_text}
        prompt_tokens = total_prompt_tokens
        completion_tokens = total_completion_tokens
        if not prompt_tokens and not completion_tokens:
            with suppress(Exception):
                prompt_tokens, completion_tokens, _total = await estimate_usage(
                    runtime.model, messages, assistant_text
                )
        message_id, _title = await _persist_async(
            conversation_id,
            "assistant",
            assistant_text,
            meta={
                "reasoning": assistant_reasoning,
                "finish_reason": finish_reason,
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                },
            },
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        final_saved = True
        yield "usage", {
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
        }
        yield "done", {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "title": title,
            "awaiting_approval": False,
        }
        break

    if not final_saved:
        message = "工具调用轮次已达上限，请缩小任务范围或继续追问。"
        await _persist_async(
            conversation_id,
            "assistant",
            message,
            meta={"error": message},
        )
        yield "text", {"delta": message}
        yield "error", {"message": message, "code": "tool_round_limit"}

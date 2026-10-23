from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import litellm

litellm.drop_params = True

from app.models import UpstreamAccount
from app.providers import get_provider
from app.services.reasoning import extract_reasoning_from_delta, extract_reasoning_text


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


def flatten_anthropic_system(system: Any) -> str | None:
    if system is None:
        return None
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        text = "".join(part.get("text", "") for part in system if isinstance(part, dict))
        return text or None
    return str(system)


def flatten_tool_result_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                texts.append(str(part.get("text") or ""))
            elif isinstance(part, dict) and "text" in part:
                texts.append(str(part.get("text") or ""))
        if texts:
            return "".join(texts)
    return json.dumps(content, ensure_ascii=False)


def _parts_to_content(parts: list[dict[str, Any]]) -> Any:
    if not parts:
        return ""
    if all(part.get("type") == "text" for part in parts):
        return "".join(str(part.get("text") or "") for part in parts)
    return parts


def anthropic_tools_to_openai(tools: list[Any]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            converted.append(tool)
            continue
        name = tool.get("name")
        if not name:
            continue
        function: dict[str, Any] = {"name": name}
        if tool.get("description"):
            function["description"] = tool["description"]
        schema = tool.get("input_schema")
        if schema is None:
            schema = tool.get("parameters")
        if schema is not None:
            function["parameters"] = schema
        converted.append({"type": "function", "function": function})
    return converted


def anthropic_tool_choice_to_openai(tool_choice: Any) -> Any:
    if isinstance(tool_choice, str):
        if tool_choice == "any":
            return "required"
        return tool_choice
    if not isinstance(tool_choice, dict):
        return None
    choice_type = tool_choice.get("type")
    if choice_type == "any":
        return "required"
    if choice_type in {"auto", "none"}:
        return choice_type
    if choice_type == "tool" and tool_choice.get("name"):
        return {"type": "function", "function": {"name": tool_choice["name"]}}
    return None


def anthropic_extra_to_openai(body: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if body.get("max_tokens") is not None:
        max_tokens = _as_token_count(body["max_tokens"])
        if max_tokens is not None:
            extra["max_tokens"] = max_tokens
    for key in ("temperature", "top_p"):
        if body.get(key) is not None:
            extra[key] = body[key]
    if body.get("stop_sequences"):
        extra["stop"] = body["stop_sequences"]
    elif body.get("stop") is not None:
        extra["stop"] = body["stop"]
    tools = body.get("tools")
    if tools:
        extra["tools"] = anthropic_tools_to_openai(tools)
    mapped_choice = anthropic_tool_choice_to_openai(body.get("tool_choice"))
    if mapped_choice is not None:
        extra["tool_choice"] = mapped_choice
    return extra


def anthropic_to_openai_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system = flatten_anthropic_system(body.get("system"))
    if system:
        messages.append({"role": "system", "content": system})
    for item in body.get("messages") or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        role = item.get("role") or "user"
        if isinstance(content, list):
            parts: list[dict[str, Any]] = []
            tool_calls = []
            thinking_parts: list[str] = []
            tool_messages: list[dict[str, Any]] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type == "text":
                    parts.append({"type": "text", "text": part.get("text", "")})
                elif part_type in {"thinking", "redacted_thinking"}:
                    thinking_text = part.get("thinking") or part.get("data") or ""
                    if thinking_text:
                        thinking_parts.append(str(thinking_text))
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
                    tool_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": part.get("tool_use_id"),
                            "content": flatten_tool_result_content(part.get("content")),
                        }
                    )
            if parts or tool_calls or thinking_parts:
                message: dict[str, Any] = {"role": "assistant" if tool_calls else role}
                message["content"] = _parts_to_content(parts) if parts else ""
                if tool_calls:
                    message["role"] = "assistant"
                    message["tool_calls"] = tool_calls
                if thinking_parts:
                    message["reasoning_content"] = "".join(thinking_parts)
                messages.append(message)
            messages.extend(tool_messages)
        else:
            outbound = {"role": role, "content": content}
            reasoning = extract_reasoning_text(item)
            if reasoning:
                outbound["reasoning_content"] = reasoning
            if item.get("tool_calls"):
                outbound["tool_calls"] = item["tool_calls"]
            messages.append(outbound)
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
    reasoning = extract_reasoning_text(message)
    if reasoning:
        content_blocks.append({"type": "thinking", "thinking": reasoning})
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


def _responses_content_to_openai(content: Any) -> Any:
    """把 Responses API 的消息 content 转成 OpenAI Chat Completions content。

    content 可以是字符串，也可以是 content parts 数组（input_text / output_text /
    input_image / refusal 等）。
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type in ("input_text", "output_text"):
            text = part.get("text")
            if isinstance(text, str) and text:
                parts.append({"type": "text", "text": text})
        elif part_type == "input_image":
            image_url = part.get("image_url")
            if isinstance(image_url, str):
                parts.append({"type": "image_url", "image_url": {"url": image_url}})
            elif isinstance(image_url, dict) and image_url.get("url"):
                parts.append({"type": "image_url", "image_url": {"url": image_url["url"]}})
        elif part_type == "refusal":
            refusal = part.get("refusal")
            if isinstance(refusal, str) and refusal:
                parts.append({"type": "text", "text": refusal})
    if not parts:
        return ""
    if all(part.get("type") == "text" for part in parts):
        return "".join(str(part.get("text") or "") for part in parts)
    return parts


def _append_tool_call(messages: list[dict[str, Any]], tool_call: dict[str, Any]) -> None:
    # Responses 里 reasoning / message 与 function_call 是各自独立的 output item，转成
    # OpenAI 消息时要合并进同一条 assistant 消息，reasoning_content 才会和 tool_calls
    # 一起回传给上游。
    if messages and messages[-1].get("role") == "assistant":
        existing = messages[-1].get("tool_calls")
        if existing is None:
            messages[-1].setdefault("content", None)
            messages[-1]["tool_calls"] = [tool_call]
        else:
            existing.append(tool_call)
        return
    messages.append({"role": "assistant", "content": None, "tool_calls": [tool_call]})


def input_to_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})
    if body.get("messages"):
        for item in body.get("messages") or []:
            if isinstance(item, dict):
                messages.append(dict(item))
        return messages
    raw_input = body.get("input")
    if isinstance(raw_input, str):
        messages.append({"role": "user", "content": raw_input})
        return messages
    if not isinstance(raw_input, list):
        return messages
    for item in raw_input:
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
            continue
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            role = item.get("role") or "user"
            entry: dict[str, Any] = {"role": role, "content": _responses_content_to_openai(item.get("content"))}
            reasoning = extract_reasoning_text(item)
            if reasoning:
                entry["reasoning_content"] = reasoning
            messages.append(entry)
            continue
        if item_type == "function_call":
            call_id = item.get("call_id") or f"call_{uuid.uuid4().hex[:8]}"
            _append_tool_call(
                messages,
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": item.get("name") or "",
                        "arguments": item.get("arguments") or "",
                    },
                },
            )
            continue
        if item_type == "function_call_output":
            call_id = item.get("call_id") or ""
            output = item.get("output")
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False) if output is not None else ""
            messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
            continue
        if item_type == "reasoning":
            summary = item.get("summary") or []
            text = "".join(
                str(part.get("text") or "")
                for part in summary
                if isinstance(part, dict) and part.get("text")
            )
            if text:
                if messages and messages[-1].get("role") == "assistant":
                    messages[-1]["reasoning_content"] = text
                else:
                    messages.append({"role": "assistant", "content": "", "reasoning_content": text})
            continue
        role = item.get("role")
        if role:
            entry = {"role": role, "content": _responses_content_to_openai(item.get("content"))}
            reasoning = extract_reasoning_text(item)
            if reasoning:
                entry["reasoning_content"] = reasoning
            messages.append(entry)
    return messages


RESPONSES_PASSTHROUGH_KEYS = (
    "background",
    "include",
    "instructions",
    "max_output_tokens",
    "metadata",
    "parallel_tool_calls",
    "previous_response_id",
    "prompt",
    "reasoning",
    "service_tier",
    "safety_identifier",
    "store",
    "temperature",
    "text",
    "tool_choice",
    "tools",
    "top_p",
    "truncation",
    "user",
)

RESPONSES_CONTENT_PART_TYPES = ("input_text", "output_text", "input_image", "input_file", "refusal")


def sanitize_responses_input(input_items: Any) -> Any:
    """过滤 Responses API 输入里上游不支持的 content part（如 Codex 的 environment_context）。

    只保留 OpenAI 官方定义的 content part 类型；识别不出的 part 直接剔除，
    若整条消息只剩不认识的 part 则丢弃该消息。
    """
    if isinstance(input_items, str) or not isinstance(input_items, list):
        return input_items
    cleaned: list[dict[str, Any]] = []
    for item in input_items:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            cleaned.append(item)
            continue
        parts = [
            part
            for part in content
            if isinstance(part, dict) and part.get("type") in RESPONSES_CONTENT_PART_TYPES
        ]
        if len(parts) == len(content):
            cleaned.append(item)
            continue
        if not parts:
            continue
        copied = dict(item)
        copied["content"] = parts
        cleaned.append(copied)
    return cleaned


def responses_extra_passthrough(body: dict[str, Any]) -> dict[str, Any]:
    """把 Responses API 的请求参数原样透传给 litellm.responses()。"""
    extra: dict[str, Any] = {}
    for key in RESPONSES_PASSTHROUGH_KEYS:
        if key in body and body[key] is not None:
            extra[key] = body[key]
    return extra


def responses_tools_to_chat(tools: Any) -> list[dict[str, Any]]:
    """把 Responses API 的工具定义转成 Chat Completions 的工具定义。

    Responses 的 function 工具是扁平的（name/parameters 在顶层），Chat 需要包一层
    `function`；内建工具（web_search 等）没有 Chat 对应物，直接丢弃。
    """
    converted: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") != "function":
            continue
        if isinstance(tool.get("function"), dict):
            converted.append(tool)
            continue
        name = tool.get("name")
        if not name:
            continue
        function: dict[str, Any] = {"name": name}
        if tool.get("description"):
            function["description"] = tool["description"]
        if tool.get("parameters") is not None:
            function["parameters"] = tool["parameters"]
        if tool.get("strict") is not None:
            function["strict"] = tool["strict"]
        converted.append({"type": "function", "function": function})
    return converted


def responses_tool_choice_to_chat(tool_choice: Any) -> Any:
    if isinstance(tool_choice, str):
        return tool_choice
    if not isinstance(tool_choice, dict):
        return None
    if tool_choice.get("type") == "function":
        name = tool_choice.get("name") or (tool_choice.get("function") or {}).get("name")
        if name:
            return {"type": "function", "function": {"name": name}}
    return None


def responses_text_format_to_chat(text: Any) -> dict[str, Any] | None:
    if not isinstance(text, dict):
        return None
    fmt = text.get("format")
    if not isinstance(fmt, dict):
        return None
    fmt_type = fmt.get("type")
    if fmt_type in (None, "text"):
        return None
    if fmt_type == "json_object":
        return {"type": "json_object"}
    if fmt_type == "json_schema":
        schema: dict[str, Any] = {"name": fmt.get("name") or "response", "schema": fmt.get("schema") or {}}
        if fmt.get("strict") is not None:
            schema["strict"] = fmt["strict"]
        return {"type": "json_schema", "json_schema": schema}
    return None


def responses_extra_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    """把 Responses API 请求参数映射成 Chat Completions 参数。

    当 Responses 请求需要回传 reasoning_content（带工具调用的多轮）时，网关改走
    Chat Completions 上游：只有 Chat 协议能看到 assistant 消息上的 reasoning_content。
    这里只映射两种协议都有的参数，Responses 专有字段（include/store/previous_response_id
    等）在 Chat 下没有对应语义，直接丢弃。
    """
    extra: dict[str, Any] = {}
    max_output = _as_token_count(body.get("max_output_tokens"))
    if max_output is not None:
        extra["max_tokens"] = max_output
    for key in ("temperature", "top_p", "parallel_tool_calls", "user", "metadata"):
        if body.get(key) is not None:
            extra[key] = body[key]
    tools = responses_tools_to_chat(body.get("tools"))
    if tools:
        extra["tools"] = tools
    mapped_choice = responses_tool_choice_to_chat(body.get("tool_choice"))
    if mapped_choice is not None:
        extra["tool_choice"] = mapped_choice
    response_format = responses_text_format_to_chat(body.get("text"))
    if response_format is not None:
        extra["response_format"] = response_format
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        effort = reasoning.get("effort")
        if isinstance(effort, str) and effort:
            extra["reasoning_effort"] = effort
    return extra


def messages_need_reasoning_replay(messages: list[dict[str, Any]]) -> bool:
    """是否需要在上游请求里回传 reasoning_content。

    只有历史里出现带 tool_calls 的 assistant 消息时才需要：思考模型在工具调用多轮
    场景下要求这些消息带上 reasoning_content，缺失即 400。
    """
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant" and message.get("tool_calls"):
            return True
    return False


async def call_chat(
    account: UpstreamAccount,
    messages: list[dict[str, Any]],
    model: str,
    stream: bool,
    extra: dict[str, Any],
    api_key: str,
) -> Any:
    return await get_provider(account.provider).complete(account, messages, model, stream, extra, api_key)


async def call_responses(
    account: UpstreamAccount,
    input_items: Any,
    model: str,
    stream: bool,
    extra: dict[str, Any],
    api_key: str,
) -> Any:
    return await get_provider(account.provider).responses(account, input_items, model, stream, extra, api_key)


async def count_openai_tokens(model: str, messages: list[dict[str, Any]]) -> int:
    try:
        # litellm.token_counter 是同步阻塞调用，丢到线程池避免卡住事件循环
        return int(await asyncio.to_thread(litellm.token_counter, model=model, messages=messages))
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


def _as_token_count(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit() or (stripped.startswith("-") and stripped[1:].isdigit()):
            return int(stripped)
    return None


def extract_usage(payload: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None, None
    prompt = _as_token_count(usage.get("prompt_tokens"))
    if prompt is None:
        prompt = _as_token_count(usage.get("input_tokens"))
    completion = _as_token_count(usage.get("completion_tokens"))
    if completion is None:
        completion = _as_token_count(usage.get("output_tokens"))
    total = _as_token_count(usage.get("total_tokens"))
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return prompt, completion, total


def pick_usage_from_chunk(chunk: dict[str, Any]) -> tuple[int | None, int | None, int | None] | None:
    usage = extract_usage(chunk)
    if usage[0] is None and usage[1] is None and usage[2] is None:
        return None
    if any((part or 0) > 0 for part in usage):
        return usage
    choices = chunk.get("choices") or []
    if not choices:
        return usage
    first = choices[0] if isinstance(choices[0], dict) else {}
    if first.get("finish_reason"):
        return usage
    return None


def ensure_stream_usage(extra: dict[str, Any]) -> dict[str, Any]:
    outbound = dict(extra)
    options = outbound.get("stream_options")
    if isinstance(options, dict):
        merged = dict(options)
        merged.setdefault("include_usage", True)
        outbound["stream_options"] = merged
    else:
        outbound["stream_options"] = {"include_usage": True}
    return outbound


async def estimate_usage(
    model: str,
    messages: list[dict[str, Any]],
    output_text: str,
) -> tuple[int, int, int]:
    prompt = await count_openai_tokens(model, messages)
    completion = await count_openai_tokens(model, [{"role": "assistant", "content": output_text or ""}])
    return prompt, completion, prompt + completion


def extract_text_from_openai_chunk(chunk: dict[str, Any]) -> str:
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    return content if isinstance(content, str) else ""


def extract_reasoning_from_openai_chunk(chunk: dict[str, Any]) -> str:
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    return extract_reasoning_from_delta(choices[0].get("delta") or {})


def openai_finish_to_anthropic(finish: str | None) -> str:
    return {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "end_turn",
    }.get(finish or "", "end_turn")


def sse_event(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


class AnthropicStreamTranslator:
    def __init__(self, message_id: str, model: str) -> None:
        self.message_id = message_id
        self.model = model
        self.block_index = -1
        self.open_kind: str | None = None
        self.reasoning = ""
        self.text = ""
        self.tools: list[dict[str, Any]] = []
        self.tool_by_openai_index: dict[int, int] = {}
        self.stop_reason = "end_turn"
        self.usage: tuple[int | None, int | None, int | None] = (None, None, None)

    def start(self) -> list[bytes]:
        return [
            sse_event(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": self.message_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": self.model,
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            )
        ]

    def feed(self, chunk: dict[str, Any]) -> list[bytes]:
        events: list[bytes] = []
        usage_candidate = pick_usage_from_chunk(chunk)
        if usage_candidate is not None:
            self.usage = usage_candidate
        choices = chunk.get("choices") or []
        if not choices:
            return events
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}
        finish = choice.get("finish_reason")
        reasoning = extract_reasoning_from_delta(delta)
        content = delta.get("content")
        if reasoning:
            events.extend(self._ensure_block("thinking"))
            self.reasoning += reasoning
            events.append(
                sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": self.block_index,
                        "delta": {"type": "thinking_delta", "thinking": reasoning},
                    },
                )
            )
        if isinstance(content, str) and content:
            events.extend(self._ensure_block("text"))
            self.text += content
            events.append(
                sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": self.block_index,
                        "delta": {"type": "text_delta", "text": content},
                    },
                )
            )
        for tool in delta.get("tool_calls") or []:
            if isinstance(tool, dict):
                events.extend(self._feed_tool(tool))
        if finish:
            self.stop_reason = openai_finish_to_anthropic(str(finish))
            events.extend(self._close_open())
        return events

    def finish(self) -> list[bytes]:
        events = self._close_open()
        events.append(
            sse_event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": self.stop_reason, "stop_sequence": None},
                    "usage": {
                        "input_tokens": self.usage[0] or 0,
                        "output_tokens": self.usage[1] or 0,
                    },
                },
            )
        )
        events.append(sse_event("message_stop", {"type": "message_stop"}))
        return events

    def payload(self) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if self.reasoning:
            content.append({"type": "thinking", "thinking": self.reasoning})
        if self.text:
            content.append({"type": "text", "text": self.text})
        for tool in self.tools:
            raw_arguments = tool.get("arguments") or ""
            try:
                parsed = json.loads(raw_arguments) if raw_arguments else {}
            except json.JSONDecodeError:
                parsed = {"raw": raw_arguments}
            content.append(
                {
                    "type": "tool_use",
                    "id": tool.get("id"),
                    "name": tool.get("name"),
                    "input": parsed,
                }
            )
        if not content:
            content = [{"type": "text", "text": ""}]
        return {
            "id": self.message_id,
            "type": "message",
            "role": "assistant",
            "content": content,
            "model": self.model,
            "stop_reason": self.stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": self.usage[0] or 0,
                "output_tokens": self.usage[1] or 0,
            },
        }

    def reasoning_map(self) -> dict[str, str]:
        if not self.reasoning:
            return {}
        return {str(tool["id"]): self.reasoning for tool in self.tools if tool.get("id")}

    def _ensure_block(self, kind: str) -> list[bytes]:
        if self.open_kind == kind:
            return []
        events = self._close_open()
        self.block_index += 1
        self.open_kind = kind
        block = {"type": "thinking", "thinking": ""} if kind == "thinking" else {"type": "text", "text": ""}
        events.append(
            sse_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": self.block_index,
                    "content_block": block,
                },
            )
        )
        return events

    def _feed_tool(self, tool: dict[str, Any]) -> list[bytes]:
        events: list[bytes] = []
        openai_index = _as_token_count(tool.get("index")) or 0
        function = tool.get("function") or {}
        if openai_index not in self.tool_by_openai_index:
            events.extend(self._close_open())
            self.block_index += 1
            self.open_kind = "tool"
            tool_id = tool.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            name = function.get("name") or ""
            record = {
                "id": tool_id,
                "name": name,
                "arguments": "",
                "block_index": self.block_index,
            }
            self.tool_by_openai_index[openai_index] = len(self.tools)
            self.tools.append(record)
            events.append(
                sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": self.block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": name,
                            "input": {},
                        },
                    },
                )
            )
        record = self.tools[self.tool_by_openai_index[openai_index]]
        if function.get("name") and not record["name"]:
            record["name"] = function["name"]
        if tool.get("id"):
            record["id"] = tool["id"]
        arguments_piece = function.get("arguments") or ""
        if arguments_piece:
            record["arguments"] += arguments_piece
            events.append(
                sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": record["block_index"],
                        "delta": {"type": "input_json_delta", "partial_json": arguments_piece},
                    },
                )
            )
        return events

    def _close_open(self) -> list[bytes]:
        if self.open_kind is None:
            return []
        events = [sse_event("content_block_stop", {"type": "content_block_stop", "index": self.block_index})]
        self.open_kind = None
        return events


async def prepare_credential(account: UpstreamAccount, db) -> str:  # type: ignore[no-untyped-def]
    return await get_provider(account.provider).prepare_credential(account, db)


def responses_sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def responses_event_to_dict(event: Any) -> dict[str, Any]:
    """把 litellm 的 Responses API 流式事件对象转成普通 dict（type 转为字符串值）。"""
    if isinstance(event, dict):
        return event
    if hasattr(event, "model_dump"):
        return event.model_dump(mode="json")
    return {}


def responses_payload_to_dict(payload: Any) -> dict[str, Any]:
    """把 litellm 的 Response 对象转成普通 dict。"""
    if isinstance(payload, dict):
        return payload
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return {}


def chat_completion_to_responses(
    payload: dict[str, Any],
    model: str,
    response_id: str | None = None,
    status: str = "completed",
) -> dict[str, Any]:
    """把 Chat Completions 响应转换成 Responses API 响应。

    网关在需要回传 reasoning_content 时改走 Chat Completions 上游，再把结果转回
    Responses 结构返回给客户端，保持客户端无感。
    """
    choice = (payload.get("choices") or [{}])[0]
    if not isinstance(choice, dict):
        choice = {}
    message = choice.get("message")
    if not isinstance(message, dict):
        message = {}
    output: list[dict[str, Any]] = []
    reasoning = extract_reasoning_text(message)
    if reasoning:
        output.append(
            {
                "id": f"rs_{uuid.uuid4().hex}",
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": reasoning}],
                "content": None,
            }
        )
    text = message.get("content")
    if isinstance(text, list):
        text = "".join(
            str(part.get("text") or "")
            for part in text
            if isinstance(part, dict) and part.get("text")
        )
    if not isinstance(text, str):
        text = ""
    tool_calls = [tool for tool in (message.get("tool_calls") or []) if isinstance(tool, dict)]
    if text or not tool_calls:
        output.append(
            {
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        )
    for tool in tool_calls:
        function = tool.get("function") or {}
        output.append(
            {
                "id": f"fc_{uuid.uuid4().hex}",
                "type": "function_call",
                "call_id": tool.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                "name": function.get("name") or "",
                "arguments": function.get("arguments") or "",
                "status": "completed",
            }
        )
    prompt, completion, total = extract_usage(payload)
    return {
        "id": response_id or f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "model": payload.get("model") or model,
        "output": output,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": {"effort": None, "summary": None},
        "store": False,
        "temperature": None,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": None,
        "truncation": "disabled",
        "usage": {
            "input_tokens": prompt or 0,
            "output_tokens": completion or 0,
            "total_tokens": total or 0,
        },
        "user": None,
        "output_text": text,
    }


class ResponsesStreamTranslator:
    """把 Chat Completions 流式 chunk 翻译成 Responses API SSE 事件。

    网关在需要回传 reasoning_content 时改走 Chat Completions 上游，这里负责把
    OpenAI 风格增量流还原成 Responses 事件序列（reasoning / message / function_call）。
    """

    def __init__(self, response_id: str, model: str) -> None:
        self.response_id = response_id
        self.model = model
        self.text = ""
        self.reasoning = ""
        self.tools: list[dict[str, Any]] = []
        self.tool_by_index: dict[int, int] = {}
        self.usage: tuple[int | None, int | None, int | None] = (None, None, None)
        self.started = False
        self._open_kind: str | None = None
        self._open_index = -1
        self._message_index = -1
        self._reasoning_index = -1
        self._message_item_id = f"msg_{uuid.uuid4().hex}"
        self._reasoning_item_id = f"rs_{uuid.uuid4().hex}"

    # ------------------------------------------------------------------ feed
    def feed(self, chunk: dict[str, Any]) -> list[bytes]:
        events: list[bytes] = []
        events.extend(self._ensure_started())
        usage_candidate = pick_usage_from_chunk(chunk)
        if usage_candidate is not None:
            self.usage = usage_candidate
        choices = chunk.get("choices") or []
        if not choices:
            return events
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}
        reasoning = extract_reasoning_from_delta(delta)
        if reasoning:
            events.extend(self._open_reasoning())
            self.reasoning += reasoning
            events.append(
                responses_sse(
                    {
                        "type": "response.reasoning_summary_text.delta",
                        "item_id": self._reasoning_item_id,
                        "output_index": self._reasoning_index,
                        "summary_index": 0,
                        "delta": reasoning,
                    }
                )
            )
        content = delta.get("content")
        if isinstance(content, str) and content:
            events.extend(self._open_message())
            self.text += content
            events.append(
                responses_sse(
                    {
                        "type": "response.output_text.delta",
                        "item_id": self._message_item_id,
                        "output_index": self._message_index,
                        "content_index": 0,
                        "delta": content,
                    }
                )
            )
        for tool in delta.get("tool_calls") or []:
            if isinstance(tool, dict):
                events.extend(self._feed_tool(tool))
        if choice.get("finish_reason"):
            events.extend(self._close_open())
        return events

    def finish(self) -> list[bytes]:
        events = self._ensure_started()
        events.extend(self._close_open())
        events.append(
            responses_sse(
                {
                    "type": "response.completed",
                    "response": self.payload(),
                }
            )
        )
        return events

    def payload(self) -> dict[str, Any]:
        output: list[dict[str, Any]] = []
        if self.reasoning:
            output.append(
                {
                    "id": self._reasoning_item_id,
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": self.reasoning}],
                    "content": None,
                }
            )
        if self.text or not self.tools:
            output.append(
                {
                    "id": self._message_item_id,
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": self.text, "annotations": []}],
                }
            )
        for tool in self.tools:
            output.append(
                {
                    "id": tool["id"],
                    "type": "function_call",
                    "call_id": tool["call_id"],
                    "name": tool["name"],
                    "arguments": tool["arguments"],
                    "status": "completed",
                }
            )
        return {
            "id": self.response_id,
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "instructions": None,
            "max_output_tokens": None,
            "model": self.model,
            "output": output,
            "parallel_tool_calls": True,
            "previous_response_id": None,
            "reasoning": {"effort": None, "summary": None},
            "store": False,
            "temperature": None,
            "text": {"format": {"type": "text"}},
            "tool_choice": "auto",
            "tools": [],
            "top_p": None,
            "truncation": "disabled",
            "usage": {
                "input_tokens": self.usage[0] or 0,
                "output_tokens": self.usage[1] or 0,
                "total_tokens": self.usage[2] or 0,
            },
            "user": None,
            "output_text": self.text,
        }

    def reasoning_map(self) -> dict[str, str]:
        if not self.reasoning:
            return {}
        return {str(tool["call_id"]): self.reasoning for tool in self.tools if tool.get("call_id")}

    # --------------------------------------------------------------- internal
    def _ensure_started(self) -> list[bytes]:
        if self.started:
            return []
        self.started = True
        created = dict(self.payload())
        created["status"] = "in_progress"
        created["output"] = []
        created["output_text"] = ""
        return [
            responses_sse({"type": "response.created", "response": created}),
            responses_sse({"type": "response.in_progress", "response": created}),
        ]

    def _next_index(self) -> int:
        index = self._open_index + 1
        self._open_index = index
        return index

    def _open_reasoning(self) -> list[bytes]:
        if self._open_kind == "reasoning":
            return []
        events = self._close_open()
        self._reasoning_index = self._next_index()
        self._open_kind = "reasoning"
        events.append(
            responses_sse(
                {
                    "type": "response.output_item.added",
                    "output_index": self._reasoning_index,
                    "item": {
                        "id": self._reasoning_item_id,
                        "type": "reasoning",
                        "summary": [],
                    },
                }
            )
        )
        events.append(
            responses_sse(
                {
                    "type": "response.reasoning_summary_part.added",
                    "item_id": self._reasoning_item_id,
                    "output_index": self._reasoning_index,
                    "summary_index": 0,
                    "part": {"type": "summary_text", "text": ""},
                }
            )
        )
        return events

    def _open_message(self) -> list[bytes]:
        if self._open_kind == "message":
            return []
        events = self._close_open()
        self._message_index = self._next_index()
        self._open_kind = "message"
        events.append(
            responses_sse(
                {
                    "type": "response.output_item.added",
                    "output_index": self._message_index,
                    "item": {
                        "id": self._message_item_id,
                        "type": "message",
                        "status": "in_progress",
                        "role": "assistant",
                        "content": [],
                    },
                }
            )
        )
        events.append(
            responses_sse(
                {
                    "type": "response.content_part.added",
                    "item_id": self._message_item_id,
                    "output_index": self._message_index,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                }
            )
        )
        return events

    def _feed_tool(self, tool: dict[str, Any]) -> list[bytes]:
        events: list[bytes] = []
        index = int(tool.get("index") or 0)
        function = tool.get("function") or {}
        if index not in self.tool_by_index:
            events.extend(self._close_open())
            output_index = self._next_index()
            self._open_kind = "tool"
            call_id = tool.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            record = {
                "id": f"fc_{uuid.uuid4().hex}",
                "call_id": call_id,
                "name": function.get("name") or "",
                "arguments": "",
                "output_index": output_index,
            }
            self.tool_by_index[index] = len(self.tools)
            self.tools.append(record)
            events.append(
                responses_sse(
                    {
                        "type": "response.output_item.added",
                        "output_index": output_index,
                        "item": {
                            "id": record["id"],
                            "type": "function_call",
                            "call_id": call_id,
                            "name": record["name"],
                            "arguments": "",
                            "status": "in_progress",
                        },
                    }
                )
            )
        record = self.tools[self.tool_by_index[index]]
        if tool.get("id"):
            record["call_id"] = str(tool["id"])
        if function.get("name") and not record["name"]:
            record["name"] = function["name"]
        arguments = function.get("arguments") or ""
        if arguments:
            record["arguments"] += arguments
            events.append(
                responses_sse(
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": record["call_id"],
                        "output_index": record["output_index"],
                        "delta": arguments,
                    }
                )
            )
        return events

    def _close_open(self) -> list[bytes]:
        if self._open_kind is None:
            return []
        kind = self._open_kind
        self._open_kind = None
        if kind == "reasoning":
            return [
                responses_sse(
                    {
                        "type": "response.reasoning_summary_text.done",
                        "item_id": self._reasoning_item_id,
                        "output_index": self._reasoning_index,
                        "summary_index": 0,
                        "text": self.reasoning,
                    }
                ),
                responses_sse(
                    {
                        "type": "response.reasoning_summary_part.done",
                        "item_id": self._reasoning_item_id,
                        "output_index": self._reasoning_index,
                        "summary_index": 0,
                        "part": {"type": "summary_text", "text": self.reasoning},
                    }
                ),
                responses_sse(
                    {
                        "type": "response.output_item.done",
                        "output_index": self._reasoning_index,
                        "item": {
                            "id": self._reasoning_item_id,
                            "type": "reasoning",
                            "summary": [{"type": "summary_text", "text": self.reasoning}],
                            "content": None,
                        },
                    }
                ),
            ]
        if kind == "message":
            item = {
                "id": self._message_item_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": self.text, "annotations": []}],
            }
            return [
                responses_sse(
                    {
                        "type": "response.output_text.done",
                        "item_id": self._message_item_id,
                        "output_index": self._message_index,
                        "content_index": 0,
                        "text": self.text,
                    }
                ),
                responses_sse(
                    {
                        "type": "response.content_part.done",
                        "item_id": self._message_item_id,
                        "output_index": self._message_index,
                        "content_index": 0,
                        "part": {"type": "output_text", "text": self.text, "annotations": []},
                    }
                ),
                responses_sse(
                    {
                        "type": "response.output_item.done",
                        "output_index": self._message_index,
                        "item": item,
                    }
                ),
            ]
        record = None
        for tool in self.tools:
            if tool["output_index"] == self._open_index:
                record = tool
                break
        if record is None:
            return []
        return [
            responses_sse(
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": record["call_id"],
                    "output_index": record["output_index"],
                    "arguments": record["arguments"],
                }
            ),
            responses_sse(
                {
                    "type": "response.output_item.done",
                    "output_index": record["output_index"],
                    "item": {
                        "id": record["id"],
                        "type": "function_call",
                        "call_id": record["call_id"],
                        "name": record["name"],
                        "arguments": record["arguments"],
                        "status": "completed",
                    },
                }
            ),
        ]


def reasoning_map_from_responses_payload(payload: dict[str, Any] | None) -> dict[str, str]:
    """从 Responses API 输出里提取 reasoning 映射（function_call call_id → reasoning 文本）。"""
    if not isinstance(payload, dict):
        return {}
    output = payload.get("output")
    if not isinstance(output, list):
        return {}
    reasoning_text: str | None = None
    mapping: dict[str, str] = {}
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "reasoning":
            summary = item.get("summary")
            if isinstance(summary, list):
                texts = [
                    str(part.get("text") or "")
                    for part in summary
                    if isinstance(part, dict) and part.get("text")
                ]
                reasoning_text = "".join(texts) or None
        elif item_type == "function_call" and reasoning_text and item.get("call_id"):
            mapping[str(item["call_id"])] = reasoning_text
    return mapping


class ResponsesStreamCollector:
    """从 litellm 的 Responses API 流式事件里收集日志所需信息（文本/推理/工具/用量）。"""

    def __init__(self, response_id: str, model: str) -> None:
        self.response_id = response_id
        self.model = model
        self.text = ""
        self.reasoning = ""
        self.tools: list[dict[str, Any]] = []
        self.usage: tuple[int | None, int | None, int | None] = (None, None, None)
        self.status = "completed"

    def feed(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                self.text += delta
        elif event_type == "response.output_item.added":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                self.tools.append(
                    {
                        "call_id": item.get("call_id") or item.get("id"),
                        "name": item.get("name") or "",
                        "arguments": item.get("arguments") or "",
                    }
                )
        elif event_type == "response.function_call_arguments.delta":
            if self.tools:
                delta = event.get("delta")
                if isinstance(delta, str):
                    self.tools[-1]["arguments"] += delta
        elif event_type == "response.output_item.done":
            item = event.get("item")
            if not isinstance(item, dict):
                return
            if item.get("type") == "reasoning":
                summary = item.get("summary")
                if isinstance(summary, list):
                    parts = [
                        str(part.get("text") or "")
                        for part in summary
                        if isinstance(part, dict) and part.get("text")
                    ]
                    if parts:
                        self.reasoning = "".join(parts)
            elif item.get("type") == "function_call":
                for record in self.tools:
                    if (record["call_id"] or "") == (item.get("call_id") or ""):
                        record["call_id"] = item.get("call_id") or record["call_id"]
                        record["name"] = item.get("name") or record["name"]
                        record["arguments"] = item.get("arguments") or record["arguments"]
                        break
        elif event_type in ("response.completed", "response.incomplete", "response.failed"):
            response = event.get("response")
            if isinstance(response, dict):
                self.status = str(response.get("status") or self.status)
                usage = extract_usage(response)
                if usage != (None, None, None):
                    self.usage = usage

    def payload(self) -> dict[str, Any]:
        output: list[dict[str, Any]] = []
        if self.reasoning:
            output.append(
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": self.reasoning}],
                }
            )
        output.append(
            {
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": self.text, "annotations": []}],
            }
        )
        for tool in self.tools:
            output.append(
                {
                    "type": "function_call",
                    "call_id": tool.get("call_id"),
                    "name": tool.get("name"),
                    "arguments": tool.get("arguments"),
                    "status": "completed",
                }
            )
        return {
            "id": self.response_id,
            "object": "response",
            "created_at": int(time.time()),
            "status": self.status,
            "model": self.model,
            "output": output,
            "usage": {
                "input_tokens": self.usage[0] or 0,
                "output_tokens": self.usage[1] or 0,
                "total_tokens": self.usage[2] or 0,
            },
            "output_text": self.text,
        }

    def reasoning_map(self) -> dict[str, str]:
        if not self.reasoning:
            return {}
        return {
            str(tool["call_id"]): self.reasoning for tool in self.tools if tool.get("call_id")
        }



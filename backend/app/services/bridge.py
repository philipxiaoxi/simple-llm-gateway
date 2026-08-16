from __future__ import annotations

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
        extra["max_tokens"] = int(body["max_tokens"])
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
                if tool_messages and not tool_calls:
                    text_parts = "".join(
                        str(part.get("text") or "") for part in parts if part.get("type") == "text"
                    )
                    if text_parts:
                        first_tool_message = tool_messages[0]
                        existing_content = first_tool_message.get("content") or ""
                        first_tool_message["content"] = (
                            f"{text_parts}\n{existing_content}" if existing_content else text_parts
                        )
                else:
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
    if messages and messages[-1].get("role") == "assistant" and messages[-1].get("tool_calls") is not None:
        messages[-1]["tool_calls"].append(tool_call)
    else:
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
        openai_index = int(tool.get("index") or 0)
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



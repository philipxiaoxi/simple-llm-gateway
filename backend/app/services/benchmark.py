from __future__ import annotations

import json
from typing import Any

from app.services.bridge import extract_usage
from app.services.reasoning import extract_reasoning_from_delta


def chunk_to_dict(chunk: Any) -> dict[str, Any]:
    if isinstance(chunk, dict):
        return chunk
    if hasattr(chunk, "model_dump"):
        dumped = chunk.model_dump()
        if isinstance(dumped, dict):
            return dumped
    if hasattr(chunk, "model_dump_json"):
        dumped = json.loads(chunk.model_dump_json())
        if isinstance(dumped, dict):
            return dumped
    return {}


def _delta_from_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    choices = chunk.get("choices") or []
    if not choices:
        return {}
    first = choices[0]
    if not isinstance(first, dict):
        return {}
    delta = first.get("delta")
    return delta if isinstance(delta, dict) else {}


def extract_answer_text(chunk: dict[str, Any]) -> str:
    content = _delta_from_chunk(chunk).get("content")
    return content if isinstance(content, str) else ""


def extract_tool_delta(chunk: dict[str, Any]) -> bool:
    tool_calls = _delta_from_chunk(chunk).get("tool_calls") or []
    for tool in tool_calls:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        if function.get("name"):
            return True
        arguments = function.get("arguments")
        if isinstance(arguments, str) and arguments:
            return True
    return False


def is_token_delta(chunk: dict[str, Any]) -> bool:
    delta = _delta_from_chunk(chunk)
    content = delta.get("content")
    if isinstance(content, str) and content:
        return True
    if extract_reasoning_from_delta(delta):
        return True
    return extract_tool_delta(chunk)


def output_tokens_from_chunk(chunk: dict[str, Any]) -> int | None:
    completion = extract_usage(chunk)[1]
    if completion is None or completion < 0:
        return None
    return completion


def compute_tokens_per_second(output_tokens: int | None, decode_ms: float | None) -> float | None:
    if output_tokens is None or output_tokens < 0:
        return None
    if decode_ms is None or decode_ms <= 0:
        return None
    return round(output_tokens / (decode_ms / 1000), 2)

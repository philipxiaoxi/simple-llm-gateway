from app.services.bridge import (
    AnthropicStreamTranslator,
    anthropic_extra_to_openai,
    anthropic_to_openai_messages,
    openai_response_to_anthropic,
)
from app.services.reasoning import (
    PLACEHOLDER_REASONING,
    inject_reasoning_into_messages,
    merge_reasoning_maps,
    parse_reasoning_json,
    reasoning_map_from_anthropic_content,
    reasoning_map_from_openai_message,
    reasoning_map_from_openai_payload,
)


def test_thinking_block_becomes_reasoning_content() -> None:
    messages = anthropic_to_openai_messages(
        {
            "system": [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}],
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "先读文件"},
                        {"type": "text", "text": "好的"},
                        {
                            "type": "tool_use",
                            "id": "call_abc",
                            "name": "Read",
                            "input": {"path": "/tmp"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_abc",
                            "content": [{"type": "text", "text": "ok"}],
                        }
                    ],
                },
            ],
        }
    )
    assert messages[0] == {"role": "system", "content": "sys"}
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "hi"
    assert messages[2]["role"] == "assistant"
    assert messages[2]["reasoning_content"] == "先读文件"
    assert messages[2]["content"] == "好的"
    assert messages[2]["tool_calls"][0]["id"] == "call_abc"
    assert messages[2]["tool_calls"][0]["function"]["name"] == "Read"
    assert messages[3] == {"role": "tool", "tool_call_id": "call_abc", "content": "ok"}


def test_anthropic_extra_converts_tools_and_drops_thinking() -> None:
    extra = anthropic_extra_to_openai(
        {
            "max_tokens": 32,
            "thinking": {"type": "adaptive"},
            "top_k": 8,
            "metadata": {"user_id": "x"},
            "tool_choice": {"type": "auto"},
            "tools": [
                {
                    "name": "Agent",
                    "description": "launch",
                    "input_schema": {"type": "object", "properties": {"prompt": {"type": "string"}}},
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    )
    assert "thinking" not in extra
    assert "top_k" not in extra
    assert "metadata" not in extra
    assert extra["max_tokens"] == 32
    assert extra["tool_choice"] == "auto"
    assert extra["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "Agent",
                "description": "launch",
                "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}}},
            },
        }
    ]


def test_openai_response_includes_thinking_block() -> None:
    payload = openai_response_to_anthropic(
        {
            "id": "chatcmpl-1",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "逐步思考",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {"name": "Read", "arguments": '{"path":"/a"}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 6},
        },
        "deepseek-v4-flash",
    )
    assert payload["stop_reason"] == "tool_use"
    assert payload["content"][0] == {"type": "thinking", "thinking": "逐步思考"}
    assert payload["content"][1]["type"] == "tool_use"
    assert payload["content"][1]["id"] == "call_1"


def test_inject_uses_store_then_placeholder() -> None:
    stored = {"call_abc": "原始推理"}
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_abc", "type": "function", "function": {"name": "Read"}}],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_new", "type": "function", "function": {"name": "Bash"}}],
        },
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "客户端带回",
            "tool_calls": [{"id": "call_keep", "type": "function", "function": {"name": "Read"}}],
        },
    ]
    inject_reasoning_into_messages(messages, stored)
    assert messages[1]["reasoning_content"] == "原始推理"
    assert messages[2]["reasoning_content"] == PLACEHOLDER_REASONING
    assert messages[3]["reasoning_content"] == "客户端带回"


def test_reasoning_map_helpers() -> None:
    mapping = reasoning_map_from_openai_message(
        {
            "reasoning_content": "abc",
            "tool_calls": [{"id": "t1"}, {"id": "t2"}],
        }
    )
    assert mapping == {"t1": "abc", "t2": "abc"}
    assert reasoning_map_from_anthropic_content(
        [
            {"type": "thinking", "thinking": "想"},
            {"type": "tool_use", "id": "t1"},
        ]
    ) == {"t1": "想"}
    dumped = parse_reasoning_json('{"by_tool_call_id": {"t1": "想"}}')
    assert dumped == {"t1": "想"}
    assert merge_reasoning_maps({"t1": "old"}, {"t1": "new", "t2": "x"}) == {"t1": "new", "t2": "x"}
    payload_map = reasoning_map_from_openai_payload(
        {
            "choices": [
                {
                    "message": {
                        "reasoning_content": "z",
                        "tool_calls": [{"id": "call_z"}],
                    }
                }
            ]
        }
    )
    assert payload_map == {"call_z": "z"}


def test_stream_translator_emits_thinking_text_and_tools() -> None:
    translator = AnthropicStreamTranslator(message_id="msg_1", model="deepseek-v4-flash")
    events = list(translator.start())
    events.extend(
        translator.feed({"choices": [{"delta": {"reasoning_content": "想"}, "index": 0}]})
    )
    events.extend(translator.feed({"choices": [{"delta": {"content": "好"}, "index": 0}]}))
    events.extend(
        translator.feed(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "Read", "arguments": ""},
                                }
                            ]
                        },
                        "index": 0,
                    }
                ]
            }
        )
    )
    events.extend(
        translator.feed(
            {
                "choices": [
                    {
                        "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"p":1}'}}]},
                        "finish_reason": "tool_calls",
                        "index": 0,
                    }
                ]
            }
        )
    )
    events.extend(translator.finish())
    decoded = b"".join(events).decode()
    assert "thinking_delta" in decoded
    assert "text_delta" in decoded
    assert "input_json_delta" in decoded
    assert '"type": "tool_use"' in decoded
    payload = translator.payload()
    assert payload["content"][0]["type"] == "thinking"
    assert payload["content"][1]["type"] == "text"
    assert payload["content"][2]["input"] == {"p": 1}
    assert translator.reasoning_map() == {"call_1": "想"}
    assert payload["stop_reason"] == "tool_use"

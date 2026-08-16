from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def _make_key(client: TestClient, auth_headers: dict[str, str], provider: str = "deepseek") -> str:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": provider, "api_key": "sk-up"},
    ).json()
    return client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account["id"]},
    ).json()["key"]


def _messages(client: TestClient, auth_headers: dict[str, str], log_id: int) -> dict:
    return client.get(
        f"/api/admin/logs/{log_id}/messages",
        headers=auth_headers,
        params={"page": 1, "page_size": 100},
    ).json()


def _log_id(client: TestClient, auth_headers: dict[str, str]) -> int:
    listed = client.get("/api/admin/logs", headers=auth_headers).json()
    return listed["items"][0]["id"]


def _responses_dict(
    text: str = "你好，世界",
    *,
    reasoning: str | None = None,
    function_calls: list[dict] | None = None,
    usage: dict | None = None,
    status: str = "completed",
) -> dict:
    output: list[dict] = []
    if reasoning:
        output.append(
            {"id": "rs_1", "type": "reasoning", "summary": [{"type": "summary_text", "text": reasoning}], "content": None}
        )
    output.append(
        {
            "id": "msg_1",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
    )
    for index, call in enumerate(function_calls or []):
        output.append(
            {
                "id": f"fc_{index}",
                "type": "function_call",
                "call_id": call.get("call_id") or f"call_{index}",
                "name": call.get("name") or "",
                "arguments": call.get("arguments") or "{}",
                "status": "completed",
            }
        )
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "status": status,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "model": "deepseek-chat",
        "output": output,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": {"effort": None, "summary": None},
        "store": True,
        "temperature": None,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": None,
        "truncation": "disabled",
        "usage": usage or {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
        "user": None,
        "output_text": text,
    }


class FakeResponsesResponse:
    def __init__(self, payload: dict | None = None) -> None:
        self._payload = payload if payload is not None else _responses_dict()

    def model_dump(self) -> dict:
        return self._payload


# ---------------------------------------------------------------- input_to_messages


def test_input_to_messages_string() -> None:
    from app.services.bridge import input_to_messages

    assert input_to_messages({"model": "x", "input": "hi"}) == [{"role": "user", "content": "hi"}]


def test_input_to_messages_instructions_become_system() -> None:
    from app.services.bridge import input_to_messages

    messages = input_to_messages({"model": "x", "instructions": "be brief", "input": "hi"})
    assert messages == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]


def test_input_to_messages_message_items_with_content_parts() -> None:
    from app.services.bridge import input_to_messages

    body = {
        "model": "x",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "a"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "b"}, {"type": "input_text", "text": "c"}]},
        ],
    }
    messages = input_to_messages(body)
    assert messages == [
        {"role": "user", "content": "a"},
        {"role": "user", "content": "bc"},
    ]


def test_input_to_messages_function_call_and_output() -> None:
    from app.services.bridge import input_to_messages

    body = {
        "model": "x",
        "input": [
            {"role": "user", "content": "查一下天气"},
            {"type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": '{"city":"SF"}'},
            {"type": "function_call_output", "call_id": "call_1", "output": "sunny"},
            {"role": "user", "content": "然后呢"},
        ],
    }
    messages = input_to_messages(body)
    assert messages[0] == {"role": "user", "content": "查一下天气"}
    assert messages[1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city":"SF"}'}}
        ],
    }
    assert messages[2] == {"role": "tool", "tool_call_id": "call_1", "content": "sunny"}
    assert messages[3] == {"role": "user", "content": "然后呢"}


def test_input_to_messages_image_part() -> None:
    from app.services.bridge import input_to_messages

    messages = input_to_messages(
        {
            "model": "x",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "看看这张图"},
                        {"type": "input_image", "image_url": "https://example.com/a.png"},
                    ],
                }
            ],
        }
    )
    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看看这张图"},
                {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
            ],
        }
    ]


def test_input_to_messages_reasoning_item() -> None:
    from app.services.bridge import input_to_messages

    messages = input_to_messages(
        {
            "model": "x",
            "input": [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "想一下"}]},
                {"role": "assistant", "content": "答案"},
            ],
        }
    )
    assert messages == [
        {"role": "assistant", "content": "", "reasoning_content": "想一下"},
        {"role": "assistant", "content": "答案"},
    ]


# ---------------------------------------------------------------- extra passthrough


def test_sanitize_responses_input_drops_unknown_parts() -> None:
    from app.services.bridge import sanitize_responses_input

    items = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "你好"},
                {"type": "environment_context", "context": [{"type": "filesystem"}]},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "environment_context", "context": [{"type": "filesystem"}]}],
        },
        {"type": "function_call", "call_id": "c1", "name": "f", "arguments": "{}"},
    ]
    cleaned = sanitize_responses_input(items)
    assert cleaned[0]["content"] == [{"type": "input_text", "text": "你好"}]
    assert all(part.get("type") != "environment_context" for part in cleaned[0]["content"])
    assert len(cleaned) == 2  # 只有 environment_context 的消息被丢弃
    assert cleaned[1]["type"] == "function_call"
    assert sanitize_responses_input("hi") == "hi"


def test_responses_extra_passthrough_whitelist() -> None:
    from app.services.bridge import responses_extra_passthrough

    extra = responses_extra_passthrough(
        {
            "model": "x",
            "stream": True,
            "max_output_tokens": 128,
            "temperature": 0.5,
            "tools": [{"type": "function", "name": "get_weather"}],
            "tool_choice": {"type": "function", "name": "get_weather"},
            "text": {"format": {"type": "json_schema", "name": "r", "schema": {"type": "object"}}},
            "reasoning": {"effort": "high"},
            "truncation": "auto",
            "store": True,
            "previous_response_id": "resp_1",
            "input": [{"role": "user", "content": "hi"}],
            "unknown_key": 1,
        }
    )
    assert extra["max_output_tokens"] == 128
    assert extra["temperature"] == 0.5
    assert extra["tools"] == [{"type": "function", "name": "get_weather"}]
    assert extra["tool_choice"] == {"type": "function", "name": "get_weather"}
    assert extra["text"] == {"format": {"type": "json_schema", "name": "r", "schema": {"type": "object"}}}
    assert extra["reasoning"] == {"effort": "high"}
    assert extra["truncation"] == "auto"
    assert extra["store"] is True
    assert extra["previous_response_id"] == "resp_1"
    assert "stream" not in extra
    assert "unknown_key" not in extra
    assert "input" not in extra


# ---------------------------------------------------------------- helpers


def test_responses_event_to_dict_passes_dict() -> None:
    from app.services.bridge import responses_event_to_dict

    event = {"type": "response.output_text.delta", "delta": "你"}
    assert responses_event_to_dict(event) is event


def test_responses_event_to_dict_converts_objects() -> None:
    from app.services.bridge import responses_event_to_dict

    class FakeEvent:
        def model_dump(self, mode: str = "python") -> dict:
            return {"type": "response.completed", "response": {"status": "completed"}}

    assert responses_event_to_dict(FakeEvent()) == {"type": "response.completed", "response": {"status": "completed"}}


def test_reasoning_map_from_responses_payload() -> None:
    from app.services.bridge import reasoning_map_from_responses_payload

    payload = _responses_dict(
        reasoning="先思考",
        function_calls=[{"call_id": "call_abc", "name": "Read", "arguments": '{"path":"/tmp"}'}],
    )
    assert reasoning_map_from_responses_payload(payload) == {"call_abc": "先思考"}
    assert reasoning_map_from_responses_payload(_responses_dict()) == {}


def test_responses_stream_collector() -> None:
    from app.services.bridge import ResponsesStreamCollector

    collector = ResponsesStreamCollector("resp_1", "deepseek-chat")
    for event in [
        {"type": "response.output_item.added", "item": {"type": "function_call", "call_id": "call_1", "name": "Read", "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "delta": '{"path":'},
        {"type": "response.function_call_arguments.delta", "delta": '"/tmp"}'},
        {"type": "response.output_item.done", "item": {"type": "reasoning", "summary": [{"type": "summary_text", "text": "想"}]}},
        {"type": "response.output_text.delta", "delta": "你"},
        {"type": "response.output_text.delta", "delta": "好"},
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "usage": {"input_tokens": 2, "output_tokens": 4, "total_tokens": 6},
            },
        },
    ]:
        collector.feed(event)

    assert collector.text == "你好"
    assert collector.reasoning == "想"
    assert collector.usage == (2, 4, 6)
    payload = collector.payload()
    assert payload["output_text"] == "你好"
    kinds = [item["type"] for item in payload["output"]]
    assert kinds == ["reasoning", "message", "function_call"]
    function_call = next(item for item in payload["output"] if item["type"] == "function_call")
    assert function_call["arguments"] == '{"path":"/tmp"}'
    assert collector.reasoning_map() == {"call_1": "想"}


# ---------------------------------------------------------------- end to end


def test_responses_endpoint_non_stream(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    captured: dict = {}

    async def fake_responses(_account, input_items, _model, _stream, extra, _credential):
        captured["input"] = input_items
        captured["extra"] = extra
        return FakeResponsesResponse()

    with patch("app.services.proxy.call_responses", new=fake_responses):
        response = client.post(
            "/v1/responses",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "deepseek-chat",
                "instructions": "be brief",
                "input": [
                    {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
                ],
                "max_output_tokens": 64,
                "stream": False,
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "response"
    assert body["status"] == "completed"
    assert body["output_text"] == "你好，世界"
    assert captured["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]
    assert captured["extra"]["max_output_tokens"] == 64
    assert "stream" not in captured["extra"]

    log_id = _log_id(client, auth_headers)
    assert client.get(f"/api/admin/logs/{log_id}", headers=auth_headers).json()["protocol"] == "openai_responses"
    items = _messages(client, auth_headers, log_id)["items"]
    assert items[0]["content"] == "你好，世界"
    assert items[1]["content"] == "hi"


def test_responses_endpoint_stream(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)

    async def fake_stream(*_args, **_kwargs):
        async def events():
            completed = _responses_dict(
                "你好",
                usage={"input_tokens": 2, "output_tokens": 2, "total_tokens": 4},
            )
            yield {"type": "response.created", "response": completed}
            yield {"type": "response.in_progress", "response": completed}
            yield {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"id": "msg_1", "type": "message", "status": "in_progress", "role": "assistant", "content": []},
            }
            yield {
                "type": "response.content_part.added",
                "item_id": "msg_1",
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            }
            yield {"type": "response.output_text.delta", "item_id": "msg_1", "output_index": 0, "content_index": 0, "delta": "你"}
            yield {"type": "response.output_text.delta", "item_id": "msg_1", "output_index": 0, "content_index": 0, "delta": "好"}
            yield {"type": "response.output_text.done", "item_id": "msg_1", "output_index": 0, "content_index": 0, "text": "你好"}
            yield {
                "type": "response.content_part.done",
                "item_id": "msg_1",
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": "你好", "annotations": []},
            }
            yield {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {"id": "msg_1", "type": "message", "status": "completed", "role": "assistant",
                         "content": [{"type": "output_text", "text": "你好", "annotations": []}]},
            }
            yield {"type": "response.completed", "response": completed}

        return events()

    with patch("app.services.proxy.call_responses", new=AsyncMock(side_effect=fake_stream)):
        with client.stream(
            "POST",
            "/v1/responses",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "stream": True, "input": "hi"},
        ) as response:
            text = "".join(response.iter_text())
    assert '"type": "response.output_text.delta"' in text
    assert '"type": "response.completed"' in text
    assert "你好" in text
    log_id = _log_id(client, auth_headers)
    detail = client.get(f"/api/admin/logs/{log_id}", headers=auth_headers).json()
    assert detail["stream"] is True
    assert detail["prompt_tokens"] == 2
    assert detail["completion_tokens"] == 2
    items = _messages(client, auth_headers, log_id)["items"]
    assert items[0]["content"] == "你好"


def test_responses_endpoint_reasoning_and_tools(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    captured: dict = {}

    async def fake_responses(_account, input_items, _model, _stream, _extra, _credential):
        captured["input"] = input_items
        return FakeResponsesResponse(
            _responses_dict(
                "",
                function_calls=[{"call_id": "call_abc", "name": "Read", "arguments": '{"path":"/tmp"}'}],
            )
        )

    with patch("app.services.proxy.call_responses", new=fake_responses):
        response = client.post(
            "/v1/responses",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "deepseek-chat",
                "input": [
                    {"role": "user", "content": "读一下"},
                    {
                        "type": "function_call",
                        "call_id": "call_abc",
                        "name": "Read",
                        "arguments": '{"path":"/tmp"}',
                    },
                    {"type": "function_call_output", "call_id": "call_abc", "output": "ok"},
                ],
            },
        )
    assert response.status_code == 200
    body = response.json()
    kinds = [item["type"] for item in body["output"]]
    assert "function_call" in kinds
    assert body["output"][-1]["name"] == "Read"
    # 原始 input items 原样传给上游（litellm 负责转换）
    assert captured["input"][1]["type"] == "function_call"
    assert captured["input"][2]["type"] == "function_call_output"


def test_responses_follow_up_reuses_same_log(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    with patch("app.services.proxy.call_responses", new=AsyncMock(return_value=FakeResponsesResponse())):
        first = client.post(
            "/v1/responses",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "input": "hi"},
        )
        assert first.status_code == 200
        second = client.post(
            "/v1/responses",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "deepseek-chat",
                "input": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "你好，世界"},
                    {"role": "user", "content": "再来一句"},
                ],
            },
        )
        assert second.status_code == 200
        third = client.post(
            "/v1/responses",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "input": "新开的对话"},
        )
        assert third.status_code == 200

    listed = client.get("/api/admin/logs", headers=auth_headers).json()
    assert listed["total"] == 2
    by_id = {item["id"]: _messages(client, auth_headers, item["id"]) for item in listed["items"]}
    continued_id = next(log_id for log_id, body in by_id.items() if body["total"] == 4)
    fresh_id = next(log_id for log_id, body in by_id.items() if body["total"] == 2)
    assert any(item["content"] == "再来一句" for item in by_id[continued_id]["items"])
    assert any(item["content"] == "新开的对话" for item in by_id[fresh_id]["items"])
    assert fresh_id != continued_id


def test_responses_normalize_input_array() -> None:
    from app.services.conversation import is_continuation, normalize_messages

    first = {"model": "x", "input": "hi"}
    second = {
        "model": "x",
        "input": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "and then?"},
        ],
    }
    unrelated = {"model": "x", "input": "unrelated"}
    assert normalize_messages("openai_responses", first) == [("user", "hi")]
    assert normalize_messages("openai_responses", second) == [
        ("user", "hi"),
        ("assistant", "ok"),
        ("user", "and then?"),
    ]
    assert is_continuation(first, second, "openai_responses") is True
    assert is_continuation(first, unrelated, "openai_responses") is False

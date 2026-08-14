from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def _make_key(client: TestClient, auth_headers: dict[str, str]) -> str:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    ).json()
    return client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account["id"]},
    ).json()["key"]


class FakeResponse:
    def model_dump(self) -> dict:
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "你好"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }


def test_openai_forward_and_log(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "你好"

    logs = client.get("/api/admin/logs", headers=auth_headers)
    assert logs.status_code == 200
    assert logs.json()["items"][0]["status"] == "success"
    detail = client.get(f"/api/admin/logs/{logs.json()['items'][0]['id']}", headers=auth_headers)
    assert detail.json()["request_body"]["messages"][0]["content"] == "hi"
    assert detail.json()["response_body"]["choices"][0]["message"]["content"] == "你好"


def test_follow_up_reuses_same_log(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
        first = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert first.status_code == 200
        second = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "你好"},
                    {"role": "user", "content": "再来一句"},
                ],
            },
        )
        assert second.status_code == 200
        third = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "新开的对话"}]},
        )
        assert third.status_code == 200

    listed = client.get("/api/admin/logs", headers=auth_headers).json()
    assert listed["total"] == 2
    details = [client.get(f"/api/admin/logs/{item['id']}", headers=auth_headers).json() for item in listed["items"]]
    continued_detail = next(item for item in details if len((item["request_body"] or {}).get("messages") or []) == 3)
    fresh_detail = next(
        item for item in details if (item["request_body"] or {}).get("messages", [{}])[0].get("content") == "新开的对话"
    )
    assert continued_detail["request_body"]["messages"][-1]["content"] == "再来一句"
    assert fresh_detail["id"] != continued_detail["id"]


def test_claude_session_id_merges_same_length_turns(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    metadata = {
        "user_id": '{"device_id":"dev","account_uuid":"","session_id":"11111111-2222-3333-4444-555555555555"}'
    }
    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
        first = client.post(
            "/v1/messages",
            headers={"Authorization": f"Bearer {key}", "x-api-key": key},
            json={
                "model": "glm-5.3",
                "max_tokens": 32,
                "metadata": metadata,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        second = client.post(
            "/v1/messages",
            headers={"Authorization": f"Bearer {key}", "x-api-key": key},
            json={
                "model": "glm-5.3",
                "max_tokens": 32,
                "metadata": metadata,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        third = client.post(
            "/v1/messages",
            headers={"Authorization": f"Bearer {key}", "x-api-key": key},
            json={
                "model": "glm-5.3",
                "max_tokens": 32,
                "metadata": {
                    "user_id": '{"device_id":"dev","account_uuid":"","session_id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}'
                },
                "messages": [{"role": "user", "content": "another chat"}],
            },
        )
    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200
    listed = client.get("/api/admin/logs", headers=auth_headers).json()
    assert listed["total"] == 2


class FakeToolResponse:
    def model_dump(self) -> dict:
        return {
            "id": "chatcmpl-tool",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "逐步思考后决定读文件",
                        "tool_calls": [
                            {
                                "id": "call_abc",
                                "type": "function",
                                "function": {"name": "Read", "arguments": '{"path":"/tmp"}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 12, "total_tokens": 20},
        }


def test_anthropic_tools_converted_to_openai(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    captured: dict = {}

    async def fake_chat(_account, _messages, _model, _stream, extra, _credential):
        captured["extra"] = extra
        return FakeResponse()

    with patch("app.services.proxy.call_chat", new=fake_chat):
        response = client.post(
            "/v1/messages",
            headers={"x-api-key": key},
            json={
                "model": "deepseek-v4-flash",
                "max_tokens": 32,
                "thinking": {"type": "adaptive"},
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [
                    {
                        "name": "Agent",
                        "description": "launch agent",
                        "input_schema": {"type": "object", "properties": {"prompt": {"type": "string"}}},
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
        )
    assert response.status_code == 200
    assert "thinking" not in captured["extra"]
    assert captured["extra"]["tools"][0]["type"] == "function"
    assert captured["extra"]["tools"][0]["function"]["name"] == "Agent"
    assert "cache_control" not in captured["extra"]["tools"][0]
    assert "cache_control" not in captured["extra"]["tools"][0]["function"]


def test_reconstruct_anthropic_from_sse() -> None:
    from app.services.proxy import reconstruct_anthropic_from_sse

    sse = """event: message_start
data: {"type": "message_start", "message": {"id": "msg_1"}}

event: content_block_delta
data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "你"}}

event: content_block_delta
data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "好"}}

event: message_delta
data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"input_tokens": 3, "output_tokens": 2}}
"""
    payload = reconstruct_anthropic_from_sse(sse, "deepseek-v4-flash")
    assert payload["content"][0]["text"] == "你好"
    assert payload["usage"]["output_tokens"] == 2


def test_reconstruct_anthropic_from_sse_thinking() -> None:
    from app.services.proxy import reconstruct_anthropic_from_sse

    sse = """event: content_block_delta
data: {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "想"}}

event: content_block_start
data: {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "call_1", "name": "Read", "input": {}}}

event: content_block_delta
data: {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "{\\"p\\":1}"}}

event: content_block_stop
data: {"type": "content_block_stop"}
"""
    payload = reconstruct_anthropic_from_sse(sse, "deepseek-v4-flash")
    assert payload["content"][0] == {"type": "thinking", "thinking": "想"}
    assert payload["content"][1]["id"] == "call_1"
    assert payload["content"][1]["input"] == {"p": 1}


def test_anthropic_forward_converts(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
        response = client.post(
            "/v1/messages",
            headers={"x-api-key": key},
            json={
                "model": "deepseek-chat",
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "message"
    assert body["content"][0]["text"] == "你好"


def test_stream_reconstructs_log(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)

    async def fake_stream(*_args, **_kwargs):
        async def chunks():
            yield {
                "choices": [{"delta": {"content": "你"}, "index": 0}],
                "usage": {},
            }
            yield {
                "choices": [{"delta": {"content": "好"}, "index": 0, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }

        return chunks()

    with patch("app.services.proxy.call_chat", new=AsyncMock(side_effect=fake_stream)):
        with client.stream(
            "POST",
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "deepseek-chat",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
        ) as response:
            text = "".join(response.iter_text())
    assert "data:" in text
    logs = client.get("/api/admin/logs", headers=auth_headers).json()
    detail = client.get(f"/api/admin/logs/{logs['items'][0]['id']}", headers=auth_headers).json()
    assert detail["stream"] is True
    assert detail["response_body"]["choices"][0]["message"]["content"] == "你好"


def test_models_endpoint(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    response = client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["data"]}
    assert "deepseek-chat" in ids


def test_anthropic_followup_injects_stored_reasoning(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    captured: list[list] = []

    async def fake_chat(_account, messages, _model, _stream, _extra, _credential):
        captured.append(messages)
        if len(captured) == 1:
            return FakeToolResponse()
        return FakeResponse()

    metadata = {"user_id": '{"device_id":"dev","account_uuid":"","session_id":"sess-reason-1"}'}
    with patch("app.services.proxy.call_chat", new=fake_chat):
        first = client.post(
            "/v1/messages",
            headers={"x-api-key": key},
            json={
                "model": "deepseek-v4-flash",
                "max_tokens": 64,
                "metadata": metadata,
                "messages": [{"role": "user", "content": "读一下"}],
                "tools": [{"name": "Read", "input_schema": {"type": "object", "properties": {}}}],
            },
        )
        assert first.status_code == 200
        assert first.json()["content"][0]["type"] == "thinking"
        assert first.json()["content"][0]["thinking"] == "逐步思考后决定读文件"
        second = client.post(
            "/v1/messages",
            headers={"x-api-key": key},
            json={
                "model": "deepseek-v4-flash",
                "max_tokens": 64,
                "metadata": metadata,
                "messages": [
                    {"role": "user", "content": "读一下"},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call_abc",
                                "name": "Read",
                                "input": {"path": "/tmp"},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "call_abc", "content": "ok"}],
                    },
                ],
                "tools": [{"name": "Read", "input_schema": {"type": "object", "properties": {}}}],
            },
        )
    assert second.status_code == 200
    assistant = next(item for item in captured[1] if item.get("tool_calls"))
    assert assistant["reasoning_content"] == "逐步思考后决定读文件"


def test_openai_followup_injects_stored_reasoning(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    captured: list[list] = []

    async def fake_chat(_account, messages, _model, _stream, _extra, _credential):
        captured.append(messages)
        if len(captured) == 1:
            return FakeToolResponse()
        return FakeResponse()

    with patch("app.services.proxy.call_chat", new=fake_chat):
        first = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "读一下"}],
                "tools": [{"type": "function", "function": {"name": "Read", "parameters": {}}}],
            },
        )
        assert first.status_code == 200
        second = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "user", "content": "读一下"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc",
                                "type": "function",
                                "function": {"name": "Read", "arguments": '{"path":"/tmp"}'},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_abc", "content": "ok"},
                ],
            },
        )
    assert second.status_code == 200
    assistant = next(item for item in captured[1] if item.get("tool_calls"))
    assert assistant["reasoning_content"] == "逐步思考后决定读文件"


def test_anthropic_stream_thinking_and_tools(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)

    async def fake_stream(*_args, **_kwargs):
        async def chunks():
            yield {"choices": [{"delta": {"reasoning_content": "想"}, "index": 0}]}
            yield {"choices": [{"delta": {"content": "好"}, "index": 0}]}
            yield {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "Read", "arguments": '{"p":1}'},
                                }
                            ]
                        },
                        "index": 0,
                        "finish_reason": "tool_calls",
                    }
                ]
            }

        return chunks()

    with patch("app.services.proxy.call_chat", new=AsyncMock(side_effect=fake_stream)):
        with client.stream(
            "POST",
            "/v1/messages",
            headers={"x-api-key": key},
            json={
                "model": "deepseek-v4-flash",
                "max_tokens": 32,
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
        ) as response:
            text = "".join(response.iter_text())
    assert "thinking_delta" in text
    assert "text_delta" in text
    assert "tool_use" in text
    logs = client.get("/api/admin/logs", headers=auth_headers).json()
    detail = client.get(f"/api/admin/logs/{logs['items'][0]['id']}", headers=auth_headers).json()
    types = [block["type"] for block in detail["response_body"]["content"]]
    assert types == ["thinking", "text", "tool_use"]


def test_missing_reasoning_uses_placeholder(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    captured: list[list] = []

    async def fake_chat(_account, messages, _model, _stream, _extra, _credential):
        captured.append(messages)
        return FakeResponse()

    with patch("app.services.proxy.call_chat", new=fake_chat):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_unknown",
                                "type": "function",
                                "function": {"name": "Read", "arguments": "{}"},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_unknown", "content": "ok"},
                ],
            },
        )
    assert response.status_code == 200
    assistant = next(item for item in captured[0] if item.get("tool_calls"))
    assert assistant["reasoning_content"] == " "
    logs = client.get("/api/admin/logs", headers=auth_headers).json()
    detail = client.get(f"/api/admin/logs/{logs['items'][0]['id']}", headers=auth_headers).json()
    logged_assistant = next(item for item in detail["request_body"]["messages"] if item.get("tool_calls"))
    assert "reasoning_content" not in logged_assistant

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def _messages(client: TestClient, auth_headers: dict[str, str], log_id: int) -> dict:
    return client.get(
        f"/api/admin/logs/{log_id}/messages",
        headers=auth_headers,
        params={"page": 1, "page_size": 100},
    ).json()


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
    items = _messages(client, auth_headers, logs.json()["items"][0]["id"])["items"]
    assert items[0]["content"] == "你好"
    assert items[1]["content"] == "hi"


def test_follow_up_without_session_creates_new_log(client: TestClient, auth_headers: dict[str, str]) -> None:
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

    # 无 session 时不猜测会话边界，每条请求独立成一条 log
    listed = client.get("/api/admin/logs", headers=auth_headers).json()
    assert listed["total"] == 3
    totals = sorted(_messages(client, auth_headers, item["id"])["total"] for item in listed["items"])
    assert totals == [2, 2, 4]


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


def test_extract_usage_keeps_zero_and_alias_keys() -> None:
    from app.services.bridge import extract_usage, pick_usage_from_chunk

    assert extract_usage({"usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}) == (0, 0, 0)
    assert extract_usage({"usage": {"input_tokens": 4, "output_tokens": 6}}) == (4, 6, 10)
    assert pick_usage_from_chunk({"choices": [{"delta": {"content": "x"}}], "usage": {}}) is None
    assert pick_usage_from_chunk({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}) == (3, 2, 5)


def test_accumulate_usage_sums_each_request_like_external_gateways() -> None:
    from app.services.proxy import accumulate_usage, billed_total

    assert billed_total(10, 20, 30) == 30
    assert billed_total(10, 20, None) == 30
    assert billed_total(None, None, None) is None
    assert accumulate_usage((10, 20, 30), (40, 15, 55)) == (50, 35, 85)
    assert accumulate_usage((10, 20, 30), (None, None, None)) == (10, 20, 30)
    assert accumulate_usage((10, 20, 30), (None, 5, None)) == (10, 25, 35)


def test_stream_requests_include_usage(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    captured: dict = {}

    async def fake_stream(_account, _messages, _model, _stream, extra, _credential):
        captured["extra"] = extra

        async def chunks():
            yield {"choices": [{"delta": {"content": "好"}, "index": 0, "finish_reason": "stop"}]}
            yield {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12}}

        return chunks()

    with patch("app.services.proxy.call_chat", new=fake_stream):
        with client.stream(
            "POST",
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        ) as response:
            "".join(response.iter_text())
    assert captured["extra"]["stream_options"]["include_usage"] is True
    detail = client.get(
        f"/api/admin/logs/{client.get('/api/admin/logs', headers=auth_headers).json()['items'][0]['id']}",
        headers=auth_headers,
    ).json()
    assert detail["prompt_tokens"] == 9
    assert detail["completion_tokens"] == 3
    assert detail["total_tokens"] == 12


def test_stream_without_usage_estimates_tokens(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)

    async def fake_stream(*_args, **_kwargs):
        async def chunks():
            yield {"choices": [{"delta": {"content": "你好世界"}, "index": 0, "finish_reason": "stop"}]}

        return chunks()

    with patch("app.services.proxy.call_chat", new=AsyncMock(side_effect=fake_stream)):
        with client.stream(
            "POST",
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "stream": True, "messages": [{"role": "user", "content": "hello there"}]},
        ) as response:
            "".join(response.iter_text())
    detail = client.get(
        f"/api/admin/logs/{client.get('/api/admin/logs', headers=auth_headers).json()['items'][0]['id']}",
        headers=auth_headers,
    ).json()
    assert detail["prompt_tokens"]
    assert detail["completion_tokens"]
    assert detail["total_tokens"] == detail["prompt_tokens"] + detail["completion_tokens"]


def test_follow_up_does_not_wipe_tokens(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    metadata = {"user_id": '{"device_id":"dev","account_uuid":"","session_id":"sess-tokens-1"}'}

    class UsageResponse:
        def model_dump(self) -> dict:
            return {
                "id": "chatcmpl-usage",
                "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
            }

    async def fake_chat(_account, _messages, _model, stream, _extra, _credential):
        if stream:

            async def chunks():
                yield {"choices": [{"delta": {"content": "再来"}, "index": 0, "finish_reason": "stop"}]}
                yield {"choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 15, "total_tokens": 55}}

            return chunks()
        return UsageResponse()

    with patch("app.services.proxy.call_chat", new=fake_chat):
        first = client.post(
            "/v1/messages",
            headers={"x-api-key": key},
            json={
                "model": "deepseek-v4-flash",
                "max_tokens": 32,
                "metadata": metadata,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert first.status_code == 200
        with client.stream(
            "POST",
            "/v1/messages",
            headers={"x-api-key": key},
            json={
                "model": "deepseek-v4-flash",
                "max_tokens": 32,
                "stream": True,
                "metadata": metadata,
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "ok"},
                    {"role": "user", "content": "again"},
                ],
            },
        ) as response:
            "".join(response.iter_text())
    listed = client.get("/api/admin/logs", headers=auth_headers).json()
    assert listed["total"] == 1
    detail = client.get(f"/api/admin/logs/{listed['items'][0]['id']}", headers=auth_headers).json()
    assert detail["prompt_tokens"] == 51
    assert detail["completion_tokens"] == 20
    assert detail["total_tokens"] == 71


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
    items = _messages(client, auth_headers, logs["items"][0]["id"])["items"]
    assert items[0]["content"] == "你好"


def test_models_endpoint(client: TestClient, auth_headers: dict[str, str]) -> None:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    ).json()
    key = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account["id"]},
    ).json()["key"]

    empty = client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert empty.status_code == 200
    assert empty.json()["data"] == []

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}

    with patch("app.providers.base.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=FakeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        fetched = client.post(f"/api/admin/accounts/{account['id']}/models", headers=auth_headers)
    assert fetched.status_code == 200

    response = client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()["data"]}
    assert set(by_id) == {"deepseek-chat", "deepseek-reasoner"}
    assert by_id["deepseek-chat"]["context_window"] == 128000
    assert by_id["deepseek-chat"]["max_output_tokens"] == 16000
    assert by_id["deepseek-reasoner"]["reasoning"] is True


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
    session_id = "sess-reason-openai-1"

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
                "session_id": session_id,
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
                "session_id": session_id,
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
    items = _messages(client, auth_headers, logs["items"][0]["id"])["items"]
    types = [block["type"] for block in items[0]["content"]]
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
    items = _messages(client, auth_headers, client.get("/api/admin/logs", headers=auth_headers).json()["items"][0]["id"])[
        "items"
    ]
    logged_assistant = next(item for item in items if item.get("tool_calls"))
    assert "reasoning_content" not in logged_assistant


def test_anthropic_upstream_passthrough_keeps_cache_control(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers, provider="anthropic_generic")
    original = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 32,
        "system": [{"type": "text", "text": "be brief", "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    }
    upstream = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "hello"}],
        "model": "claude-sonnet-4-6",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 4},
    }

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return upstream

        text = "{}"

    with patch("app.providers.anthropic_generic.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.post = AsyncMock(return_value=FakeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        response = client.post("/v1/messages", headers={"x-api-key": key, "anthropic-beta": "prompt-caching-2024-07-31"}, json=original)

    assert response.status_code == 200
    assert response.json()["type"] == "message"
    assert response.json()["content"][0]["text"] == "hello"
    posted = instance.post.await_args
    assert posted.args[0] == "https://api.anthropic.com/v1/messages"
    assert posted.kwargs["json"]["system"][0]["cache_control"]["type"] == "ephemeral"
    assert posted.kwargs["headers"]["x-api-key"] == "sk-up"
    assert posted.kwargs["headers"]["anthropic-version"] == "2023-06-01"
    assert posted.kwargs["headers"]["anthropic-beta"] == "prompt-caching-2024-07-31"
    detail = client.get(
        f"/api/admin/logs/{client.get('/api/admin/logs', headers=auth_headers).json()['items'][0]['id']}",
        headers=auth_headers,
    ).json()
    assert detail["prompt_tokens"] == 10
    assert detail["completion_tokens"] == 4


def test_openai_inbound_to_anthropic_upstream_uses_anthropic_prefix(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    key = _make_key(client, auth_headers, provider="anthropic_generic")
    captured: dict = {}

    async def fake_completion(**kwargs):
        captured.update(kwargs)
        return FakeResponse()

    with patch("app.providers.anthropic_generic.litellm.acompletion", new=fake_completion):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200
    assert captured["model"] == "anthropic/claude-sonnet-4-6"
    assert captured["api_base"] == "https://api.anthropic.com"
    assert captured["extra_headers"]["x-api-key"] == "sk-up"


def test_anthropic_upstream_stream_passthrough(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers, provider="anthropic_generic")
    sse = (
        b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_s","type":"message","role":"assistant","content":[],"model":"claude-sonnet-4-6","stop_reason":null,"usage":{"input_tokens":3,"output_tokens":0}}}\n\n'
        b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
        b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}\n\n'
        b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
        b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"input_tokens":3,"output_tokens":1}}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )

    class FakeStreamResponse:
        status_code = 200

        async def aiter_bytes(self):
            yield sse

        async def aread(self) -> bytes:
            return sse

    class FakeStream:
        async def __aenter__(self):
            return FakeStreamResponse()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    class FakeClient:
        def stream(self, *args, **kwargs):
            captured["url"] = args[1] if len(args) > 1 else kwargs.get("url")
            captured["json"] = kwargs.get("json")
            captured["headers"] = kwargs.get("headers")
            return FakeStream()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    captured: dict = {}
    with patch("app.services.proxy.httpx.AsyncClient", return_value=FakeClient()):
        with client.stream(
            "POST",
            "/v1/messages",
            headers={"x-api-key": key},
            json={"model": "claude-sonnet-4-6", "max_tokens": 16, "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        ) as response:
            text = "".join(response.iter_text())
    assert "text_delta" in text
    assert "message_stop" in text
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["json"]["stream"] is True
    assert captured["headers"]["x-api-key"] == "sk-up"
    items = _messages(
        client,
        auth_headers,
        client.get("/api/admin/logs", headers=auth_headers).json()["items"][0]["id"],
    )["items"]
    assert items[0]["content"][0]["text"] == "hi"


def test_anthropic_count_tokens_passthrough(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers, provider="anthropic_generic")

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"input_tokens": 17}

        text = "{}"

    with patch("app.providers.anthropic_generic.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.post = AsyncMock(return_value=FakeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        response = client.post(
            "/v1/messages/count_tokens",
            headers={"x-api-key": key},
            json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200
    assert response.json() == {"input_tokens": 17}
    assert instance.post.await_args.args[0] == "https://api.anthropic.com/v1/messages/count_tokens"

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
    with patch("app.services.proxy.call_anthropic", new=AsyncMock(return_value=FakeAnthropicResponse())):
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


class FakeAnthropicResponse:
    def model_dump(self) -> dict:
        return {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "你好"}],
            "model": "deepseek-chat",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 3, "output_tokens": 2},
        }


def test_anthropic_tools_passed_to_litellm(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    captured: dict = {}

    async def fake_anthropic(_account, body, _stream, _credential):
        captured["tools"] = body.get("tools")
        return FakeAnthropicResponse()

    with patch("app.services.proxy.call_anthropic", new=fake_anthropic):
        response = client.post(
            "/v1/messages",
            headers={"x-api-key": key},
            json={
                "model": "deepseek-v4-flash",
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [
                    {
                        "name": "Agent",
                        "description": "launch agent",
                        "input_schema": {"type": "object", "properties": {"prompt": {"type": "string"}}},
                    }
                ],
            },
        )
    assert response.status_code == 200
    assert captured["tools"][0]["name"] == "Agent"
    assert "input_schema" in captured["tools"][0]
    assert "function" not in captured["tools"][0]


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


def test_call_anthropic_drops_thinking(monkeypatch) -> None:
    from app.models import UpstreamAccount
    from app.services import bridge

    captured: dict = {}

    async def fake_messages(**kwargs):
        captured.update(kwargs)
        return FakeAnthropicResponse()

    monkeypatch.setattr(bridge.litellm, "anthropic_messages", fake_messages)
    account = UpstreamAccount(
        name="oc",
        provider="opencode_go",
        auth_type="api_key",
        base_url="https://opencode.ai/zen/go",
        status="active",
    )

    import asyncio

    asyncio.run(
        bridge.call_anthropic(
            account,
            {
                "model": "deepseek-v4-flash",
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "hi"}],
                "thinking": {"type": "enabled", "budget_tokens": 8000},
                "tools": [{"name": "Agent", "input_schema": {"type": "object", "properties": {}}}],
            },
            False,
            "sk-up",
        )
    )
    assert "thinking" not in captured
    assert captured["drop_params"] is True
    assert captured["tools"][0]["name"] == "Agent"


def test_anthropic_forward_converts(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    with patch("app.services.proxy.call_anthropic", new=AsyncMock(return_value=FakeAnthropicResponse())):
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

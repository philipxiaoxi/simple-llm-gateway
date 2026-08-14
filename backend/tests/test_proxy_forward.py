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
    assert logs.json()[0]["status"] == "success"
    detail = client.get(f"/api/admin/logs/{logs.json()[0]['id']}", headers=auth_headers)
    assert detail.json()["request_body"]["messages"][0]["content"] == "hi"
    assert detail.json()["response_body"]["choices"][0]["message"]["content"] == "你好"


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
    detail = client.get(f"/api/admin/logs/{logs[0]['id']}", headers=auth_headers).json()
    assert detail["stream"] is True
    assert detail["response_body"]["choices"][0]["message"]["content"] == "你好"


def test_models_endpoint(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    response = client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["data"]}
    assert "deepseek-chat" in ids

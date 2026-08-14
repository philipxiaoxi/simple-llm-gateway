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
    detail = client.get(f"/api/admin/logs/{logs[0]['id']}", headers=auth_headers).json()
    assert detail["stream"] is True
    assert detail["response_body"]["choices"][0]["message"]["content"] == "你好"


def test_models_endpoint(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    response = client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["data"]}
    assert "deepseek-chat" in ids

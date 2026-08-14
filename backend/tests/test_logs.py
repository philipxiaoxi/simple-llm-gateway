from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class FakeResponse:
    def model_dump(self) -> dict:
        return {
            "id": "chatcmpl-test",
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


def test_dashboard_counts(client: TestClient, auth_headers: dict[str, str]) -> None:
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
    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
        )
    dashboard = client.get("/api/admin/dashboard", headers=auth_headers)
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["account_count"] == 1
    assert body["today_requests"] >= 1


def test_logs_are_paginated(client: TestClient, auth_headers: dict[str, str]) -> None:
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
    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
        for index in range(3):
            client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "deepseek-chat", "messages": [{"role": "user", "content": f"hi-{index}"}]},
            )

    first = client.get("/api/admin/logs", headers=auth_headers, params={"page": 1, "page_size": 2})
    assert first.status_code == 200
    body = first.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2
    second = client.get("/api/admin/logs", headers=auth_headers, params={"page": 2, "page_size": 2})
    assert len(second.json()["items"]) == 1
    ids = {item["id"] for item in body["items"] + second.json()["items"]}
    assert len(ids) == 3

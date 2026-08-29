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
    assert body["today_tokens"] >= 2
    assert body["total_requests"] >= 1
    assert body["total_tokens"] >= 2
    assert body["benchmark_count"] == 0
    assert body["key_count"] == 1
    assert body["tool_count"] >= 0
    assert body["agent_count"] == 0
    assert body["agent_online_count"] == 0
    assert body["leaderboard_top"] == []
    assert body["benchmark_speed_top"] == []


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
    assert body["items"][0]["api_key_name"] == "k"
    assert body["items"][0]["account_name"] == "DS"
    assert body["items"][0]["account_source"] == "upstream"


def test_log_messages_are_paginated_newest_first(client: TestClient, auth_headers: dict[str, str]) -> None:
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
    history = [{"role": "user", "content": f"turn-{index}"} for index in range(5)]
    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "messages": history},
        )
    log_id = client.get("/api/admin/logs", headers=auth_headers).json()["items"][0]["id"]
    header = client.get(f"/api/admin/logs/{log_id}", headers=auth_headers, params={"include_bodies": False})
    assert header.status_code == 200
    assert header.json()["request_body"] is None
    assert header.json()["response_body"] is None

    first = client.get(
        f"/api/admin/logs/{log_id}/messages",
        headers=auth_headers,
        params={"page": 1, "page_size": 3},
    )
    assert first.status_code == 200
    body = first.json()
    assert body["total"] == 6
    assert body["page"] == 1
    assert [item["role"] for item in body["items"]] == ["assistant", "user", "user"]
    assert body["items"][0]["content"] == "ok"
    assert [item["content"] for item in body["items"][1:]] == ["turn-4", "turn-3"]

    second = client.get(
        f"/api/admin/logs/{log_id}/messages",
        headers=auth_headers,
        params={"page": 2, "page_size": 3},
    )
    assert [item["content"] for item in second.json()["items"]] == ["turn-2", "turn-1", "turn-0"]

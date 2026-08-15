from fastapi.testclient import TestClient


def _account(client: TestClient, auth_headers: dict[str, str]) -> int:
    response = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    )
    return response.json()["id"]


def test_create_key_returns_plaintext_and_can_reveal_later(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    account_id = _account(client, auth_headers)
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "同事A", "account_id": account_id},
    )
    assert created.status_code == 200
    plaintext = created.json()["key"]
    assert plaintext.startswith("sk-")
    key_id = created.json()["id"]

    listed = client.get("/api/admin/keys", headers=auth_headers)
    assert listed.json()[0]["key"] is None

    detail = client.get(f"/api/admin/keys/{key_id}", headers=auth_headers)
    assert detail.json()["key"] == plaintext


def test_cc_switch_links(client: TestClient, auth_headers: dict[str, str]) -> None:
    from unittest.mock import AsyncMock, patch

    account_id = _account(client, auth_headers)

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
        client.post(f"/api/admin/accounts/{account_id}/models", headers=auth_headers)

    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "同事A", "account_id": account_id},
    ).json()
    response = client.get(f"/api/admin/keys/{created['id']}/cc-switch", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["models"] == ["deepseek-chat", "deepseek-reasoner"]
    targets = {item["app"]: item for item in body["targets"]}
    assert targets["opencode"]["needs_dialog"] is True
    assert "url" not in targets["opencode"]
    assert targets["claude"]["needs_dialog"] is True

    built = client.post(
        f"/api/admin/keys/{created['id']}/cc-switch",
        headers=auth_headers,
        json={"app": "claude", "model": "deepseek-reasoner", "haiku_model": "deepseek-chat"},
    )
    assert built.status_code == 200
    assert "model=deepseek-reasoner" in built.json()["url"]
    assert "haikuModel=deepseek-chat" in built.json()["url"]

    opencode = client.post(
        f"/api/admin/keys/{created['id']}/cc-switch",
        headers=auth_headers,
        json={"app": "opencode", "model": "deepseek-reasoner"},
    )
    assert opencode.status_code == 200
    assert "model=deepseek-reasoner" in opencode.json()["url"]
    assert "app=opencode" in opencode.json()["url"]


def test_disable_key(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account_id},
    )
    key_id = created.json()["id"]
    updated = client.patch(f"/api/admin/keys/{key_id}", headers=auth_headers, json={"status": "disabled"})
    assert updated.json()["status"] == "disabled"


def test_delete_key_keeps_request_logs(client: TestClient, auth_headers: dict[str, str]) -> None:
    from unittest.mock import AsyncMock, patch

    account_id = _account(client, auth_headers)
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account_id},
    )
    key_id = created.json()["id"]
    plaintext = created.json()["key"]

    class FakeResponse:
        def model_dump(self) -> dict:
            return {
                "id": "chatcmpl-test",
                "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
        proxied = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {plaintext}"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert proxied.status_code == 200
    logs = client.get("/api/admin/logs", headers=auth_headers)
    assert logs.json()["total"] == 1
    log_id = logs.json()["items"][0]["id"]

    deleted = client.delete(f"/api/admin/keys/{key_id}", headers=auth_headers)
    assert deleted.status_code == 200
    listed = client.get("/api/admin/keys", headers=auth_headers)
    assert listed.json() == []
    remaining_logs = client.get("/api/admin/logs", headers=auth_headers)
    assert remaining_logs.json()["total"] == 1
    leftover = remaining_logs.json()["items"][0]
    assert leftover["api_key_id"] == key_id
    assert leftover["api_key_name"] == "k"
    assert leftover["account_name"] == "DS"
    messages = client.get(f"/api/admin/logs/{log_id}/messages", headers=auth_headers)
    assert messages.status_code == 200
    assert messages.json()["total"] >= 1

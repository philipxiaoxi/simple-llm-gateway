from fastapi.testclient import TestClient


def _make_key(client: TestClient, auth_headers: dict[str, str], status: str = "active") -> tuple[str, int]:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up", "status": status},
    ).json()
    key = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account["id"]},
    ).json()
    return key["key"], account["id"]


def test_invalid_key_openai_shape(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-nope"},
        json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_invalid_key_anthropic_shape(client: TestClient) -> None:
    response = client.post(
        "/v1/messages",
        headers={"x-api-key": "sk-nope"},
        json={"model": "deepseek-chat", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401
    assert response.json()["type"] == "error"
    assert response.json()["error"]["type"] == "authentication_error"


def test_disabled_key(client: TestClient, auth_headers: dict[str, str]) -> None:
    plaintext, _ = _make_key(client, auth_headers)
    key_id = client.get("/api/admin/keys", headers=auth_headers).json()[0]["id"]
    client.patch(f"/api/admin/keys/{key_id}", headers=auth_headers, json={"status": "disabled"})
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401


def test_disabled_account(client: TestClient, auth_headers: dict[str, str]) -> None:
    plaintext, account_id = _make_key(client, auth_headers)
    client.patch(f"/api/admin/accounts/{account_id}", headers=auth_headers, json={"status": "disabled"})
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 403

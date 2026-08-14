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

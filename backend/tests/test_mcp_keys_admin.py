from __future__ import annotations

from fastapi.testclient import TestClient


def test_reveal_mcp_key_returns_full_plaintext(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "reveal-me", "capability_ids": ["knowledge"]},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["key"].startswith("mcp-")

    revealed = client.get(f"/api/admin/mcp/keys/{body['id']}/reveal", headers=auth_headers)
    assert revealed.status_code == 200, revealed.text
    assert revealed.json()["key"] == body["key"]
    assert revealed.json()["name"] == "reveal-me"


def test_reveal_existing_key_from_list(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "listed", "capability_ids": ["knowledge"]},
    )
    key_id = created.json()["id"]
    listed = client.get("/api/admin/mcp/keys", headers=auth_headers).json()
    row = next(item for item in listed if item["id"] == key_id)
    assert "key" not in row or row.get("key") is None

    revealed = client.get(f"/api/admin/mcp/keys/{key_id}/reveal", headers=auth_headers)
    assert revealed.status_code == 200
    assert revealed.json()["key"].startswith("mcp-")


def test_reveal_unknown_key_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get("/api/admin/mcp/keys/999999/reveal", headers=auth_headers).status_code == 404


def test_reveal_requires_admin(client: TestClient) -> None:
    assert client.get("/api/admin/mcp/keys/1/reveal").status_code == 401

from __future__ import annotations

from fastapi.testclient import TestClient

BUILTINS = ("knowledge", "docparse", "site")


def test_catalog_exposes_integration_metadata(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/admin/mcp/catalog", headers=auth_headers)
    assert response.status_code == 200, response.text
    by_id = {item["capability_id"]: item for item in response.json()["items"]}
    for capability_id in BUILTINS:
        entry = by_id[capability_id]
        assert entry["integration"]["rest_endpoints"], capability_id
        assert entry["integration"]["notes"], capability_id


def test_key_integration_marks_authorized_capabilities(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "integration", "capability_ids": ["knowledge"]},
    )
    assert created.status_code == 201, created.text

    response = client.get(f"/api/admin/mcp/keys/{created.json()['id']}/integration", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["origin"] == "http://testserver"
    assert body["mcp_url"] == "http://testserver/mcp"
    assert body["rest_base_url"] == "http://testserver"
    assert body["key"]["capability_ids"] == ["knowledge"]
    assert body["key"].get("key") is None

    by_id = {item["capability_id"]: item for item in body["capabilities"]}
    assert by_id["knowledge"]["authorized"] is True
    assert by_id["docparse"]["authorized"] is False
    assert by_id["site"]["authorized"] is False
    assert by_id["knowledge"]["integration"]["rest_endpoints"]


def test_key_integration_requires_admin_and_existing_key(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get("/api/admin/mcp/keys/1/integration").status_code == 401
    assert client.get("/api/admin/mcp/keys/999999/integration", headers=auth_headers).status_code == 404

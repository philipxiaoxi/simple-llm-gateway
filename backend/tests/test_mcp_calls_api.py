from __future__ import annotations

from fastapi.testclient import TestClient


def _create_key(client: TestClient, auth_headers: dict[str, str], name: str) -> str:
    response = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": name, "capability_ids": ["knowledge"]},
    )
    assert response.status_code == 201, response.text
    return response.json()["key"]


def _seed_calls(client: TestClient, auth_headers: dict[str, str]) -> str:
    key = _create_key(client, auth_headers, "calls")
    auth = {"Authorization": f"Bearer {key}"}

    ok = client.post("/v1/capabilities/knowledge/list", headers=auth, json={})
    assert ok.status_code == 200, ok.text

    failed = client.post(
        "/v1/capabilities/knowledge/search",
        headers=auth,
        json={"kb_id": "missing-kb", "query": "anything", "mode": "fulltext"},
    )
    assert failed.status_code == 404, failed.text
    return key


def test_call_logs_paginated(client: TestClient, auth_headers: dict[str, str]):
    _seed_calls(client, auth_headers)

    first = client.get("/api/admin/mcp/calls?limit=1", headers=auth_headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["limit"] == 1
    assert body["offset"] == 0
    assert len(body["items"]) == 1
    assert body["total"] >= 2

    second = client.get("/api/admin/mcp/calls?limit=1&offset=1", headers=auth_headers).json()
    assert len(second["items"]) == 1
    assert second["items"][0]["id"] != body["items"][0]["id"]


def test_call_logs_filters(client: TestClient, auth_headers: dict[str, str]):
    _seed_calls(client, auth_headers)

    success = client.get("/api/admin/mcp/calls?success=true", headers=auth_headers).json()
    assert success["total"] >= 1
    assert all(item["success"] is True for item in success["items"])

    failed = client.get("/api/admin/mcp/calls?success=false", headers=auth_headers).json()
    assert failed["total"] >= 1
    assert all(item["success"] is False for item in failed["items"])
    assert all(item["error_message"] for item in failed["items"])

    by_capability = client.get("/api/admin/mcp/calls?capability_id=knowledge", headers=auth_headers).json()
    assert by_capability["total"] >= 2

    by_keyword = client.get("/api/admin/mcp/calls?q=search", headers=auth_headers).json()
    assert by_keyword["total"] >= 1
    assert all(item["operation"] == "search" for item in by_keyword["items"])

    none = client.get("/api/admin/mcp/calls?capability_id=not-exists", headers=auth_headers).json()
    assert none["total"] == 0
    assert none["items"] == []

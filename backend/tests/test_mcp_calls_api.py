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


def _create_key_with(client: TestClient, auth_headers: dict[str, str], name: str, capabilities: list[str]) -> str:
    response = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": name, "capability_ids": capabilities},
    )
    assert response.status_code == 201, response.text
    return response.json()["key"]


def test_site_rest_writes_call_logs(client: TestClient, auth_headers: dict[str, str]) -> None:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("index.html", b"<p>logged</p>")
    key = _create_key_with(client, auth_headers, "site-calls", ["site"])
    headers = {"Authorization": f"Bearer {key}"}
    created = client.post(
        "/v1/sites",
        headers=headers,
        data={"slug": "logged-site"},
        files={"file": ("site.zip", buffer.getvalue(), "application/zip")},
    )
    assert created.status_code == 202, created.text
    listed = client.get("/v1/sites", headers=headers)
    assert listed.status_code == 200, listed.text
    denied = client.get("/v1/sites", headers={"Authorization": f"Bearer {_create_key(client, auth_headers, 'kb-only')}"})
    assert denied.status_code == 403

    logs = client.get("/api/admin/mcp/calls?capability_id=site", headers=auth_headers).json()
    operations = {item["operation"] for item in logs["items"]}
    assert {"deploy", "list"} <= operations
    assert any(item["success"] is True and item["operation"] == "deploy" for item in logs["items"])
    assert any(item["success"] is False and item["operation"] == "list" for item in logs["items"])


def test_diagram_rest_writes_call_logs(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    from app.capabilities.diagram import renderer

    def fake_run(diagram_type, source_path, out_path, quality, sandbox):
        out_path.write_text("<!doctype html><html><body>logged</body></html>", encoding="utf-8")
        return 0, '{"ok": true, "diagnostics": []}', ""

    monkeypatch.setattr(renderer, "_run_cli", fake_run)
    key = _create_key_with(client, auth_headers, "diagram-calls", ["diagram"])
    headers = {"Authorization": f"Bearer {key}"}
    created = client.post(
        "/v1/diagrams",
        headers=headers,
        json={
            "type": "architecture",
            "source": {
                "schema_version": 1,
                "diagram_type": "architecture",
                "meta": {"title": "logged", "output": "logged.html"},
                "components": [{"id": "a", "type": "external", "label": "A"}],
            },
            "slug": "logged-diagram",
        },
    )
    assert created.status_code == 202, created.text
    detail = client.get("/v1/diagrams/logged-diagram", headers=headers)
    assert detail.status_code == 200, detail.text

    logs = client.get("/api/admin/mcp/calls?capability_id=diagram", headers=auth_headers).json()
    operations = {item["operation"] for item in logs["items"]}
    assert {"create", "status"} <= operations
    assert all(item["capability_id"] == "diagram" for item in logs["items"])


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

from __future__ import annotations

from fastapi.testclient import TestClient


def test_mcp_key_isolated_from_chat_key(client: TestClient, auth_headers: dict[str, str]):
    # create chat-like key path is unrelated; forged sk- must not open capabilities
    response = client.get("/v1/capabilities", headers={"Authorization": "Bearer sk-not-a-real-key"})
    assert response.status_code == 401

    created = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "agent", "capability_ids": ["knowledge"]},
    )
    assert created.status_code == 201, created.text
    mcp_key = created.json()["key"]
    assert mcp_key.startswith("mcp-")

    listed = client.get("/v1/capabilities", headers={"Authorization": f"Bearer {mcp_key}"})
    assert listed.status_code == 200, listed.text
    ids = [item["capability_id"] for item in listed.json()]
    assert "knowledge" in ids


def test_knowledge_upload_and_fulltext_search(client: TestClient, auth_headers: dict[str, str]):
    key_resp = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "kb", "capability_ids": ["knowledge"]},
    )
    assert key_resp.status_code == 201, key_resp.text
    mcp_key = key_resp.json()["key"]

    base = client.post(
        "/api/admin/mcp/knowledge/bases",
        headers=auth_headers,
        json={"name": "手册", "description": "demo"},
    )
    assert base.status_code == 201, base.text
    kb_id = base.json()["id"]

    doc = client.post(
        f"/api/admin/mcp/knowledge/bases/{kb_id}/documents",
        headers=auth_headers,
        json={"text": "阿尔法火箭推进剂配方说明，含液氧煤油组合。", "source_name": "a.txt"},
    )
    assert doc.status_code == 201, doc.text
    assert doc.json()["chunk_count"] >= 1

    search = client.post(
        "/v1/capabilities/knowledge/search",
        headers={"Authorization": f"Bearer {mcp_key}"},
        json={"kb_id": kb_id, "query": "推进剂", "mode": "fulltext", "top_k": 5},
    )
    assert search.status_code == 200, search.text
    body = search.json()
    assert body["mode"] == "fulltext"
    assert isinstance(body["hits"], list)

    hybrid = client.post(
        "/v1/capabilities/knowledge/search",
        headers={"Authorization": f"Bearer {mcp_key}"},
        json={"kb_id": kb_id, "query": "推进剂", "mode": "hybrid", "top_k": 5},
    )
    assert hybrid.status_code == 200, hybrid.text


def test_unauthorized_capability_forbidden(client: TestClient, auth_headers: dict[str, str]):
    response = client.post(
        "/v1/capabilities/knowledge/search",
        json={"kb_id": "x", "query": "y"},
    )
    assert response.status_code == 401


def test_knowledge_job_queue_and_retry(client: TestClient, auth_headers: dict[str, str]):
    base = client.post(
        "/api/admin/mcp/knowledge/bases",
        headers=auth_headers,
        json={"name": "jobs", "description": ""},
    )
    assert base.status_code == 201, base.text
    kb_id = base.json()["id"]

    created = client.post(
        "/api/admin/mcp/knowledge/jobs",
        headers=auth_headers,
        json={"kb_id": kb_id, "text": "任务队列测试文本 beta", "source_name": "j.txt"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "queued"

    listed = client.get("/api/admin/mcp/knowledge/jobs", headers=auth_headers, params={"kb_id": kb_id})
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1

    job_id = created.json()["id"]
    detail = client.get(f"/api/admin/mcp/knowledge/jobs/{job_id}", headers=auth_headers)
    assert detail.status_code == 200

    # 排队中不能重试
    retry = client.post(f"/api/admin/mcp/knowledge/jobs/{job_id}/retry", headers=auth_headers)
    assert retry.status_code == 409

    # 删除
    assert client.delete(f"/api/admin/mcp/knowledge/jobs/{job_id}", headers=auth_headers).status_code == 204
    assert client.get(f"/api/admin/mcp/knowledge/jobs/{job_id}", headers=auth_headers).status_code == 404


def test_admin_catalog_and_generic_dispatch(client: TestClient, auth_headers: dict[str, str]):
    catalog = client.get("/api/admin/mcp/catalog", headers=auth_headers)
    assert catalog.status_code == 200, catalog.text
    items = catalog.json()["items"]
    assert any(item["capability_id"] == "knowledge" for item in items)
    knowledge = next(item for item in items if item["capability_id"] == "knowledge")
    assert knowledge["admin_path"] == "/mcp-plaza/knowledge"
    assert any(tool["name"] == "knowledge_search" for tool in knowledge["tools"])

    key_resp = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "generic", "capability_ids": ["knowledge"]},
    )
    assert key_resp.status_code == 201, key_resp.text
    mcp_key = key_resp.json()["key"]

    base = client.post(
        "/api/admin/mcp/knowledge/bases",
        headers=auth_headers,
        json={"name": "g", "description": ""},
    )
    assert base.status_code == 201
    kb_id = base.json()["id"]
    client.post(
        f"/api/admin/mcp/knowledge/bases/{kb_id}/documents",
        headers=auth_headers,
        json={"text": "通用分发测试文本 alpha", "source_name": "t.txt"},
    )

    generic = client.post(
        "/v1/capabilities/knowledge/list",
        headers={"Authorization": f"Bearer {mcp_key}"},
        json={},
    )
    assert generic.status_code == 200, generic.text
    assert "bases" in generic.json()

    search = client.post(
        "/v1/capabilities/knowledge/search",
        headers={"Authorization": f"Bearer {mcp_key}"},
        json={"kb_id": kb_id, "query": "alpha", "mode": "fulltext"},
    )
    assert search.status_code == 200, search.text

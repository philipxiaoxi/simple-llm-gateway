from __future__ import annotations

import asyncio
from datetime import timedelta

from fastapi.testclient import TestClient

from app.clock import utcnow


def _create_key(client: TestClient, auth_headers: dict[str, str], name: str, capabilities: list[str]) -> str:
    response = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": name, "capability_ids": capabilities},
    )
    assert response.status_code == 201, response.text
    return response.json()["key"]


def _create_base(
    client: TestClient,
    auth_headers: dict[str, str],
    name: str,
    **extra,
) -> dict:
    payload = {"name": name, "description": ""}
    payload.update(extra)
    response = client.post("/api/admin/mcp/knowledge/bases", headers=auth_headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _add_text(client: TestClient, auth_headers: dict[str, str], kb_id: str, text: str) -> None:
    response = client.post(
        f"/api/admin/mcp/knowledge/bases/{kb_id}/documents",
        headers=auth_headers,
        json={"text": text, "source_name": "s.txt"},
    )
    assert response.status_code == 201, response.text


def test_private_base_hidden_from_mcp(client: TestClient, auth_headers: dict[str, str]):
    key = _create_key(client, auth_headers, "reader", ["knowledge"])
    base = _create_base(client, auth_headers, "secret", scope="private")
    _add_text(client, auth_headers, base["id"], "私有内容")

    # 管理端仍可检索
    admin = client.post(
        f"/api/admin/mcp/knowledge/bases/{base['id']}/search",
        headers=auth_headers,
        json={"query": "私有", "mode": "fulltext"},
    )
    assert admin.status_code == 200, admin.text

    # MCP Key 看不到，也搜不到
    listed = client.post(
        "/v1/capabilities/knowledge/list",
        headers={"Authorization": f"Bearer {key}"},
        json={},
    )
    assert listed.status_code == 200, listed.text
    assert all(item["id"] != base["id"] for item in listed.json()["bases"])

    search = client.post(
        "/v1/capabilities/knowledge/search",
        headers={"Authorization": f"Bearer {key}"},
        json={"kb_id": base["id"], "query": "私有", "mode": "fulltext"},
    )
    assert search.status_code == 404, search.text


def test_restricted_base_allows_only_whitelisted_key(client: TestClient, auth_headers: dict[str, str]):
    allowed = _create_key(client, auth_headers, "allowed", ["knowledge"])
    blocked = _create_key(client, auth_headers, "blocked", ["knowledge"])
    keys = client.get("/api/admin/mcp/keys", headers=auth_headers).json()
    allowed_id = next(item["id"] for item in keys if item["name"] == "allowed")

    base = _create_base(
        client,
        auth_headers,
        "restricted",
        scope="restricted",
        allowed_mcp_key_ids=[allowed_id],
    )
    _add_text(client, auth_headers, base["id"], "受限内容 alpha")

    ok = client.post(
        "/v1/capabilities/knowledge/search",
        headers={"Authorization": f"Bearer {allowed}"},
        json={"kb_id": base["id"], "query": "alpha", "mode": "fulltext"},
    )
    assert ok.status_code == 200, ok.text

    denied = client.post(
        "/v1/capabilities/knowledge/search",
        headers={"Authorization": f"Bearer {blocked}"},
        json={"kb_id": base["id"], "query": "alpha", "mode": "fulltext"},
    )
    assert denied.status_code == 404, denied.text


def test_embedding_model_change_marks_stale_and_reindex(client: TestClient, auth_headers: dict[str, str]):
    base = _create_base(client, auth_headers, "stale", embedding_model="embedding-1")
    _add_text(client, auth_headers, base["id"], "模型变更加测试")

    changed = client.patch(
        f"/api/admin/mcp/knowledge/bases/{base['id']}",
        headers=auth_headers,
        json={"embedding_model": "embedding-2"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["stale_document_count"] == 1
    assert changed.json()["signature_mismatch"] is True

    reindex = client.post(
        f"/api/admin/mcp/knowledge/bases/{base['id']}/reindex",
        headers=auth_headers,
        json={"only_stale": True},
    )
    assert reindex.status_code == 200, reindex.text
    assert reindex.json()["created"] == 1

    # 重建必须真正成功：旧签名集合被清空，文档恢复 ready，检索不再降级
    asyncio.run(_process_pending_reembed(base["id"]))

    rows = client.get("/api/admin/mcp/knowledge/bases", headers=auth_headers).json()
    detail = next(item for item in rows if item["id"] == base["id"])
    assert detail["stale_document_count"] == 0
    assert detail["signature_mismatch"] is False

    from app.services import chroma_store

    assert chroma_store.collection_signature(base["id"]) == "embedding-2|auto"

    search = client.post(
        f"/api/admin/mcp/knowledge/bases/{base['id']}/search",
        headers=auth_headers,
        json={"query": "模型变更加测试", "mode": "hybrid"},
    )
    assert search.status_code == 200, search.text
    assert search.json()["degraded"] is False


async def _process_pending_reembed(kb_id: str) -> None:
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import KnowledgeIngestJob
    from app.services import knowledge_jobs

    session = get_session_factory()()
    try:
        job_id = session.scalar(
            select(KnowledgeIngestJob.id)
            .where(KnowledgeIngestJob.kb_id == kb_id, KnowledgeIngestJob.kind == "reembed")
            .order_by(KnowledgeIngestJob.id.desc())
        )
    finally:
        session.close()
    assert job_id is not None
    await knowledge_jobs._process_job(job_id)


def test_reindex_only_stale_includes_failed_documents(client: TestClient, auth_headers: dict[str, str]):
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import KnowledgeDocument

    base = _create_base(client, auth_headers, "failed-retry")
    _add_text(client, auth_headers, base["id"], "retry me")

    session = get_session_factory()()
    try:
        document = session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.kb_id == base["id"]))
        document.vector_status = "failed"
        document.vector_error = "boom"
        session.commit()
    finally:
        session.close()

    rows = client.get("/api/admin/mcp/knowledge/bases", headers=auth_headers).json()
    detail = next(item for item in rows if item["id"] == base["id"])
    assert detail["stale_document_count"] == 1

    reindex = client.post(
        f"/api/admin/mcp/knowledge/bases/{base['id']}/reindex",
        headers=auth_headers,
        json={"only_stale": True},
    )
    assert reindex.status_code == 200, reindex.text
    assert reindex.json()["created"] == 1


def test_cross_base_search(client: TestClient, auth_headers: dict[str, str]):
    first = _create_base(client, auth_headers, "kb-a")
    second = _create_base(client, auth_headers, "kb-b")
    _add_text(client, auth_headers, first["id"], "alpha crossbase retrieval")
    _add_text(client, auth_headers, second["id"], "beta crossbase retrieval")

    response = client.post(
        f"/api/admin/mcp/knowledge/bases/{first['id']}/search",
        headers=auth_headers,
        json={"kb_ids": [first["id"], second["id"]], "query": "crossbase", "mode": "fulltext", "top_k": 10},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["kb_ids"]) == 2
    assert {hit["kb_id"] for hit in body["hits"]} == {first["id"], second["id"]}


def test_mcp_tools_filtered_by_key_capabilities(client: TestClient, auth_headers: dict[str, str]):
    from app.db import get_session_factory
    from app.mcp_server import _current_mcp_key_id, build_mcp_app, mcp, register_all_tools
    from app.models import McpKey
    from sqlalchemy import select

    build_mcp_app()
    register_all_tools()

    enabled = _create_key(client, auth_headers, "with-knowledge", ["knowledge"])
    plain = _create_key(client, auth_headers, "without-knowledge", ["other-capability"])

    session = get_session_factory()()
    try:
        enabled_id = session.scalar(select(McpKey.id).where(McpKey.key_prefix == enabled[:12]))
        plain_id = session.scalar(select(McpKey.id).where(McpKey.key_prefix == plain[:12]))
    finally:
        session.close()

    async def names_for(key_id: int | None) -> list[str]:
        token = _current_mcp_key_id.set(key_id)
        try:
            return [tool.name for tool in await mcp.list_tools()]
        finally:
            _current_mcp_key_id.reset(token)

    with_knowledge = asyncio.run(names_for(enabled_id))
    assert "knowledge_search" in with_knowledge

    without = asyncio.run(names_for(plain_id))
    assert without == []

    anonymous = asyncio.run(names_for(None))
    assert anonymous == []


def test_retention_cleanup_removes_finished_jobs(client: TestClient, auth_headers: dict[str, str]):
    from app.db import get_session_factory
    from app.models import KnowledgeIngestJob
    from app.services import knowledge_retention

    base = _create_base(client, auth_headers, "retention")
    created = client.post(
        "/api/admin/mcp/knowledge/jobs",
        headers=auth_headers,
        json={"kb_id": base["id"], "text": "保留期测试", "source_name": "r.txt"},
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]

    session = get_session_factory()()
    try:
        job = session.get(KnowledgeIngestJob, job_id)
        job.status = "succeeded"
        job.stage = "done"
        job.finished_at = utcnow() - timedelta(days=90)
        session.commit()
    finally:
        session.close()

    result = knowledge_retention.cleanup_once()
    assert result["jobs"] >= 1

    session = get_session_factory()()
    try:
        assert session.get(KnowledgeIngestJob, job_id) is None
    finally:
        session.close()

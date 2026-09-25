from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from app.capabilities.docparse.convert import ConvertResult, markdown_source_name
from app.capabilities.docparse.errors import DocParseError
from app.services.knowledge.text_files import is_office_file, is_text_file


def test_office_extension_is_not_plain_text() -> None:
    assert is_office_file("notes.docx")
    assert is_office_file("scan.pdf")
    assert not is_text_file("notes.docx", b"PK")
    assert not is_text_file("scan.pdf", b"%PDF-1.4")
    assert markdown_source_name("docs/notes.docx") == "docs/notes.md"


def test_admin_upload_uses_converter_and_can_ingest(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    monkeypatch.setattr(
        "app.capabilities.docparse.jobs.convert_bytes",
        lambda name, raw: ConvertResult(markdown="# 安装说明\n\n先准备密钥。", page_count=1),
    )
    monkeypatch.setattr("app.capabilities.docparse.convert.assert_supported", lambda name, raw: ".docx")
    created = client.post(
        "/api/admin/mcp/knowledge/bases",
        headers=auth_headers,
        json={"name": "文档库", "description": ""},
    )
    assert created.status_code == 201, created.text
    kb_id = created.json()["id"]

    response = client.post(
        "/api/admin/mcp/docparse/jobs",
        headers=auth_headers,
        files={"file": ("notes.docx", b"fake-docx", "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    detail = client.get(f"/api/admin/mcp/docparse/jobs/{body['job_id']}", headers=auth_headers)
    assert "安装说明" in detail.json()["markdown"]

    ingested = client.post(
        f"/api/admin/mcp/docparse/jobs/{body['job_id']}/ingest",
        headers=auth_headers,
        data={"kb_id": kb_id},
    )
    assert ingested.status_code == 200, ingested.text
    jobs = client.get(f"/api/admin/mcp/knowledge/jobs?kb_id={kb_id}", headers=auth_headers)
    assert jobs.json()["items"][0]["source_name"] == "notes.md"


def test_knowledge_batch_converts_office_and_skips_failure(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    def fake_convert(name: str, raw: bytes) -> ConvertResult:
        if name.endswith(".pdf"):
            raise DocParseError("PDF 无法读取")
        return ConvertResult(markdown="# converted", page_count=1)

    monkeypatch.setattr("app.routers.admin_mcp_knowledge_jobs.convert_bytes", fake_convert)
    created = client.post(
        "/api/admin/mcp/knowledge/bases",
        headers=auth_headers,
        json={"name": "混合入库", "description": ""},
    )
    kb_id = created.json()["id"]
    response = client.post(
        "/api/admin/mcp/knowledge/jobs/upload-batch",
        headers=auth_headers,
        data={"kb_id": kb_id},
        files=[
            ("files", ("notes.docx", b"fake-docx", "application/octet-stream")),
            ("files", ("plain.txt", b"plain body", "text/plain")),
            ("files", ("broken.pdf", b"%PDF", "application/pdf")),
        ],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created"] == 2
    assert body["skipped"][0]["name"] == "broken.pdf"
    jobs = client.get(f"/api/admin/mcp/knowledge/jobs?kb_id={kb_id}", headers=auth_headers)
    names = sorted(item["source_name"] for item in jobs.json()["items"])
    assert names == ["notes.md", "plain.txt"]


def test_mcp_key_auth_and_job_isolation(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    monkeypatch.setattr("app.capabilities.docparse.convert.assert_supported", lambda name, raw: ".docx")
    monkeypatch.setattr("app.capabilities.docparse.jobs.convert_bytes", lambda name, raw: ConvertResult(markdown="# ok"))
    owner = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "parse", "capability_ids": ["docparse"]},
    )
    owner_key = owner.json()["key"]
    other = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "kb-only", "capability_ids": ["knowledge"]},
    )
    payload = {"filename": "notes.docx", "content_base64": base64.b64encode(b"fake-docx").decode()}
    denied = client.post(
        "/v1/capabilities/docparse/convert",
        headers={"Authorization": f"Bearer {other.json()['key']}"},
        json=payload,
    )
    assert denied.status_code == 403
    created = client.post(
        "/v1/capabilities/docparse/convert",
        headers={"Authorization": f"Bearer {owner_key}"},
        json=payload,
    )
    assert created.status_code == 200, created.text
    stranger = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "other-parse", "capability_ids": ["docparse"]},
    )
    foreign = client.get(
        f"/v1/capabilities/docparse/jobs/{created.json()['job_id']}",
        headers={"Authorization": f"Bearer {stranger.json()['key']}"},
    )
    assert foreign.status_code == 404


def test_chat_key_cannot_call_docparse(client: TestClient) -> None:
    response = client.post(
        "/v1/capabilities/docparse/convert",
        headers={"Authorization": "Bearer sk-not-mcp"},
        json={"filename": "a.docx", "content_base64": "YQ=="},
    )
    assert response.status_code == 401



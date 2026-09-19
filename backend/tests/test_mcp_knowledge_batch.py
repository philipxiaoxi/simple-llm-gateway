from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.services.knowledge.text_files import clean_source_name, decode_text, is_text_file


def test_is_text_file_detection() -> None:
    assert is_text_file("readme.md", b"# title\nbody")
    assert is_text_file("note.txt", b"hello")
    assert is_text_file("Makefile", b"all:\n\techo hi\n")
    assert not is_text_file("photo.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00")
    assert not is_text_file("archive.zip", b"PK\x03\x04\x00\x00")
    assert not is_text_file("empty.txt", b"")


def test_decode_text_handles_utf8_and_gbk() -> None:
    assert decode_text("中文内容".encode()) == "中文内容"
    assert decode_text("中文内容".encode("gb18030")) == "中文内容"
    assert decode_text(b"\x00\x01\x02binary") is None


def test_clean_source_name_strips_parent_paths() -> None:
    assert clean_source_name("docs/sub/../a.md") == "docs/sub/a.md"
    assert clean_source_name("../etc/passwd") == "etc/passwd"
    assert clean_source_name("") == "upload.txt"


def _create_base(client: TestClient, auth_headers: dict[str, str]) -> str:
    response = client.post(
        "/api/admin/mcp/knowledge/bases",
        headers=auth_headers,
        json={"name": "批量入库", "description": ""},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_batch_upload_creates_one_job_per_text_file(client: TestClient, auth_headers: dict[str, str]):
    kb_id = _create_base(client, auth_headers)
    files = [
        ("files", ("a.md", b"# alpha\nmarkdown body", "text/markdown")),
        ("files", ("b.txt", b"beta plain text", "text/plain")),
        ("files", ("Makefile", b"all:\n\techo hi\n", "application/octet-stream")),
        ("files", ("photo.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00", "image/png")),
    ]
    relative = json.dumps(["docs/a.md", "docs/b.txt", "Makefile", "docs/photo.png"])
    response = client.post(
        "/api/admin/mcp/knowledge/jobs/upload-batch",
        headers=auth_headers,
        data={"kb_id": kb_id, "relative_paths": relative},
        files=files,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created"] == 3
    assert len(body["job_ids"]) == 3
    assert [item["name"] for item in body["skipped"]] == ["docs/photo.png"]
    assert body["skipped"][0]["reason"] == "非文本文件"

    jobs = client.get(f"/api/admin/mcp/knowledge/jobs?kb_id={kb_id}", headers=auth_headers).json()
    names = sorted(item["source_name"] for item in jobs["items"])
    assert names == ["Makefile", "docs/a.md", "docs/b.txt"]


def test_batch_upload_rejects_too_many_files(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    from app.config import get_settings

    kb_id = _create_base(client, auth_headers)
    monkeypatch.setattr(get_settings(), "mcp_knowledge_batch_max_files", 1, raising=False)
    files = [
        ("files", ("a.txt", b"alpha", "text/plain")),
        ("files", ("b.txt", b"beta", "text/plain")),
    ]
    response = client.post(
        "/api/admin/mcp/knowledge/jobs/upload-batch",
        headers=auth_headers,
        data={"kb_id": kb_id},
        files=files,
    )
    assert response.status_code == 413

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Skill, SkillClassificationSettings, UpstreamAccount
from app.services import skills as skills_service


SKILL_MD = """---
name: planning-with-files
description: Use persistent files to plan long tasks.
license: MIT
compatibility: Claude, Codex, Cursor
---

# Planning with files

Keep the plan on disk so the agent can resume.
"""

SECOND_SKILL_MD = """---
name: demo-writer
description: Help write release notes.
license: Apache-2.0
---

Write concise release notes.
"""


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_upload_zip_list_download_and_delete(client: TestClient, auth_headers: dict[str, str]) -> None:
    payload = _zip_bytes(
        {
            "planning-with-files/SKILL.md": SKILL_MD,
            "planning-with-files/references/plan.md": "# plan\n",
        }
    )
    uploaded = client.post(
        "/api/admin/skills/upload",
        headers=auth_headers,
        files=[("files", ("planning.zip", payload, "application/zip"))],
        data={"category": "自动识别"},
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["created"] == 1
    item = body["items"][0]
    assert item["slug"] == "planning-with-files"
    assert item["name"] == "planning-with-files"
    assert item["file_count"] == 2
    assert "Claude" in item["platforms"]
    assert item["category"] in {"办公效率", "编程开发", "记忆与上下文", "其他", "自动化", "Agent工具与平台"}

    listed = client.get("/api/admin/skills", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == item["id"]
    listed_alias = client.get("/api/admin/skills/list", headers=auth_headers)
    assert listed_alias.status_code == 200
    assert listed_alias.json()["total"] == 1
    assert listed.headers.get("cache-control") == "no-store"
    assert listed_alias.headers.get("cache-control") == "no-store"

    detail = client.get(f"/api/admin/skills/{item['id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert "Keep the plan on disk" in detail.json()["skill_md"]
    assert {file["path"] for file in detail.json()["files"]} == {"SKILL.md", "references/plan.md"}

    downloaded = client.get(f"/api/admin/skills/{item['id']}/download", headers=auth_headers)
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        names = archive.namelist()
    assert "planning-with-files/SKILL.md" in names
    assert "planning-with-files/references/plan.md" in names

    single = client.get(f"/api/admin/skills/{item['id']}/files/references/plan.md", headers=auth_headers)
    assert single.status_code == 200
    assert single.content == b"# plan\n"

    deleted = client.delete(f"/api/admin/skills/{item['id']}", headers=auth_headers)
    assert deleted.status_code == 200
    missing = client.get(f"/api/admin/skills/{item['id']}", headers=auth_headers)
    assert missing.status_code == 404


def test_upload_directory_and_collection_zip(client: TestClient, auth_headers: dict[str, str]) -> None:
    directory = client.post(
        "/api/admin/skills/upload",
        headers=auth_headers,
        files=[
            ("files", ("writer/SKILL.md", SECOND_SKILL_MD.encode("utf-8"), "text/markdown")),
            ("files", ("writer/examples/note.md", b"example", "text/markdown")),
        ],
        data={"category": "内容创作"},
    )
    assert directory.status_code == 200, directory.text
    assert directory.json()["created"] == 1
    assert directory.json()["items"][0]["category"] == "内容创作"

    collection = _zip_bytes(
        {
            "skills/planning-with-files/SKILL.md": SKILL_MD,
            "skills/demo-writer/SKILL.md": SECOND_SKILL_MD,
        }
    )
    uploaded = client.post(
        "/api/admin/skills/upload",
        headers=auth_headers,
        files=[("files", ("collection.zip", collection, "application/zip"))],
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["created"] == 2
    slugs = {item["slug"] for item in uploaded.json()["items"]}
    assert "planning-with-files" in slugs
    assert "demo-writer-2" in slugs

    listed = client.get("/api/admin/skills?q=planning", headers=auth_headers)
    assert listed.json()["total"] == 1
    dashboard = client.get("/api/admin/dashboard", headers=auth_headers)
    assert dashboard.json()["skill_count"] == 3


def test_skill_analysis_sends_text_files_and_directory_structure(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    payload = _zip_bytes(
        {
            "analyzed/SKILL.md": SKILL_MD,
            "analyzed/references/guide.md": "g" * 2001,
            "analyzed/scripts/run.py": "print('run')\n",
            "analyzed/assets/logo.png": b"\x89PNG\r\n\x1a\n\x00binary",
            "analyzed/LICENSE": "Example license text",
        }
    )
    uploaded = client.post(
        "/api/admin/skills/upload",
        headers=auth_headers,
        files=[("files", ("analyzed.zip", payload, "application/zip"))],
    )
    skill_id = uploaded.json()["items"][0]["id"]

    from app.db import get_session_factory

    with get_session_factory()() as db:
        account = UpstreamAccount(
            name="analysis-account",
            provider="openai_generic",
            auth_type="api_key",
            base_url="https://example.test/v1",
            models_json='["analysis-model"]',
            api_key_encrypted="encrypted",
        )
        db.add(account)
        db.flush()
        settings = db.scalar(select(SkillClassificationSettings))
        assert settings is not None
        settings.report_enabled = True
        settings.report_account_id = account.id
        settings.report_model = "analysis-model"
        db.commit()

    captured: dict = {}

    async def fake_call_chat(*args, **kwargs):
        captured["messages"] = args[1]
        return {"choices": [{"message": {"content": '{"summary":"ok"}'}}]}

    monkeypatch.setattr(skills_service, "call_chat", fake_call_chat)
    monkeypatch.setattr(skills_service, "require_upstream_credential", lambda account: "credential")

    response = client.post(f"/api/admin/skills/{skill_id}/analysis", headers=auth_headers)
    assert response.status_code == 200, response.text
    sent_skill = json.loads(captured["messages"][1]["content"])["skill"]
    assert set(sent_skill["directory_structure"]) == {
        "SKILL.md",
        "assets/",
        "assets/logo.png",
        "references/",
        "references/guide.md",
        "scripts/",
        "scripts/run.py",
        "LICENSE",
    }
    assert {item["path"] for item in sent_skill["text_files"]} == {
        "SKILL.md",
        "references/guide.md",
        "scripts/run.py",
        "LICENSE",
    }
    assert next(item for item in sent_skill["text_files"] if item["path"] == "references/guide.md")["content"] == "g" * 2000


def test_upload_rejects_missing_skill_md_and_zip_slip(client: TestClient, auth_headers: dict[str, str]) -> None:
    missing = client.post(
        "/api/admin/skills/upload",
        headers=auth_headers,
        files=[("files", ("notes.md", b"hello", "text/markdown"))],
    )
    assert missing.status_code == 400
    assert "SKILL.md" in missing.json()["detail"]

    slipped = _zip_bytes({"../evil.txt": "nope", "demo/SKILL.md": SKILL_MD})
    rejected = client.post(
        "/api/admin/skills/upload",
        headers=auth_headers,
        files=[("files", ("evil.zip", slipped, "application/zip"))],
    )
    assert rejected.status_code == 400

    unauth = client.get("/api/admin/skills")
    assert unauth.status_code == 401


def test_update_skill_metadata(client: TestClient, auth_headers: dict[str, str], tmp_path: Path) -> None:
    uploaded = client.post(
        "/api/admin/skills/upload",
        headers=auth_headers,
        files=[("files", ("demo/SKILL.md", SKILL_MD.encode("utf-8"), "text/markdown"))],
    )
    skill_id = uploaded.json()["items"][0]["id"]
    updated = client.patch(
        f"/api/admin/skills/{skill_id}",
        headers=auth_headers,
        json={"name": "文件规划", "category": "办公效率", "description": "用文件承接长任务"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "文件规划"
    assert updated.json()["category"] == "办公效率"
    assert updated.json()["description"] == "用文件承接长任务"
    assert tmp_path.exists()


def test_replace_skill_keeps_id_and_clears_analysis(client: TestClient, auth_headers: dict[str, str]) -> None:
    uploaded = client.post(
        "/api/admin/skills/upload",
        headers=auth_headers,
        files=[("files", ("demo/SKILL.md", SKILL_MD.encode("utf-8"), "text/markdown"))],
    )
    skill_id = uploaded.json()["items"][0]["id"]
    replacement = SKILL_MD.replace("Keep the plan on disk", "Keep the updated plan on disk")
    replaced = client.post(
        f"/api/admin/skills/{skill_id}/replace",
        headers=auth_headers,
        files=[("files", ("demo/SKILL.md", replacement.encode("utf-8"), "text/markdown"))],
        data={"category": "办公效率"},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["id"] == skill_id
    assert replaced.json()["category"] == "办公效率"
    detail = client.get(f"/api/admin/skills/{skill_id}", headers=auth_headers).json()
    assert "updated plan" in detail["skill_md"]
    assert detail["analysis"] is None


def test_bulk_update_matches_existing_slugs(client: TestClient, auth_headers: dict[str, str]) -> None:
    first = client.post(
        "/api/admin/skills/upload",
        headers=auth_headers,
        files=[("files", ("planning/SKILL.md", SKILL_MD.encode("utf-8"), "text/markdown"))],
    ).json()["items"][0]
    second = client.post(
        "/api/admin/skills/upload",
        headers=auth_headers,
        files=[("files", ("writer/SKILL.md", SECOND_SKILL_MD.encode("utf-8"), "text/markdown"))],
    ).json()["items"][0]
    updated_md = SKILL_MD.replace("Keep the plan on disk", "Bulk updated content")
    bulk = client.post(
        "/api/admin/skills/bulk-update",
        headers=auth_headers,
        files=[
            ("files", ("planning-with-files/SKILL.md", updated_md.encode("utf-8"), "text/markdown")),
            ("files", ("missing/SKILL.md", SECOND_SKILL_MD.replace("demo-writer", "missing").encode("utf-8"), "text/markdown")),
        ],
    )
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["created"] == 1
    assert bulk.json()["items"][0]["id"] == first["id"]
    assert any(item["name"] == "missing" for item in bulk.json()["skipped"])
    assert client.get(f"/api/admin/skills/{second['id']}", headers=auth_headers).json()["skill_md"] == SECOND_SKILL_MD
    assert "Bulk updated content" in client.get(f"/api/admin/skills/{first['id']}", headers=auth_headers).json()["skill_md"]


def test_manage_skill_categories(client: TestClient, auth_headers: dict[str, str]) -> None:
    listed = client.get("/api/admin/skills/categories", headers=auth_headers)
    assert listed.status_code == 200, listed.text
    names = {item["name"] for item in listed.json()["items"]}
    assert "其他" in names
    assert "内容创作" in names
    protected = next(item for item in listed.json()["items"] if item["name"] == "其他")
    assert protected["is_protected"] is True

    created = client.post(
        "/api/admin/skills/categories",
        headers=auth_headers,
        json={"name": "内部工具", "keywords": ["internal", "内部"]},
    )
    assert created.status_code == 200, created.text
    category_id = created.json()["id"]
    assert created.json()["name"] == "内部工具"
    assert "internal" in created.json()["keywords"]

    duplicate = client.post(
        "/api/admin/skills/categories",
        headers=auth_headers,
        json={"name": "内部工具"},
    )
    assert duplicate.status_code == 400

    reserved = client.post(
        "/api/admin/skills/categories",
        headers=auth_headers,
        json={"name": "全部"},
    )
    assert reserved.status_code == 400

    uploaded = client.post(
        "/api/admin/skills/upload",
        headers=auth_headers,
        files=[("files", ("demo/SKILL.md", SKILL_MD.encode("utf-8"), "text/markdown"))],
        data={"category": "内部工具"},
    )
    assert uploaded.status_code == 200, uploaded.text
    skill_id = uploaded.json()["items"][0]["id"]
    assert uploaded.json()["items"][0]["category"] == "内部工具"

    renamed = client.patch(
        f"/api/admin/skills/categories/{category_id}",
        headers=auth_headers,
        json={"name": "团队工具", "keywords": ["team", "团队"]},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "团队工具"
    assert renamed.json()["count"] == 1

    detail = client.get(f"/api/admin/skills/{skill_id}", headers=auth_headers)
    assert detail.json()["category"] == "团队工具"

    chips = client.get("/api/admin/skills/list", headers=auth_headers)
    chip_names = [item["name"] for item in chips.json()["categories"]]
    assert "团队工具" in chip_names
    assert "内部工具" not in chip_names

    blocked = client.delete(f"/api/admin/skills/categories/{protected['id']}", headers=auth_headers)
    assert blocked.status_code == 400

    deleted = client.delete(f"/api/admin/skills/categories/{category_id}", headers=auth_headers)
    assert deleted.status_code == 200
    moved = client.get(f"/api/admin/skills/{skill_id}", headers=auth_headers)
    assert moved.json()["category"] == "其他"

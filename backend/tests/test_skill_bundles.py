from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import get_session_factory
from app.models import Admin, Skill

SKILL_MD_A = """---
name: pack-alpha
description: Alpha skill for bundles.
---

# Alpha
"""

SKILL_MD_B = """---
name: pack-beta
description: Beta skill for bundles.
---

# Beta
"""


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _upload_skill(client: TestClient, auth_headers: dict[str, str], slug: str, content: str) -> dict:
    payload = _zip_bytes({f"{slug}/SKILL.md": content})
    response = client.post(
        "/api/admin/skills/upload",
        headers=auth_headers,
        files=[("files", (f"{slug}.zip", payload, "application/zip"))],
    )
    assert response.status_code == 200, response.text
    return response.json()["items"][0]


def test_bundle_crud_and_members(client: TestClient, auth_headers: dict[str, str]) -> None:
    alpha = _upload_skill(client, auth_headers, "pack-alpha", SKILL_MD_A)
    beta = _upload_skill(client, auth_headers, "pack-beta", SKILL_MD_B)

    created = client.post(
        "/api/admin/skill-bundles",
        headers=auth_headers,
        json={"name": "办公套件", "description": "常用办公 skills"},
    )
    assert created.status_code == 200, created.text
    bundle = created.json()
    assert bundle["name"] == "办公套件"
    assert bundle["member_count"] == 0

    empty_name = client.post("/api/admin/skill-bundles", headers=auth_headers, json={"name": "  "})
    assert empty_name.status_code == 400

    listed = client.get("/api/admin/skill-bundles", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    added = client.post(
        f"/api/admin/skill-bundles/{bundle['id']}/members",
        headers=auth_headers,
        json={"skill_ids": [alpha["id"], beta["id"], alpha["id"], 999999]},
    )
    assert added.status_code == 200, added.text
    body = added.json()
    assert body["added"] == 2
    reasons = {item["skill_id"]: item["reason"] for item in body["skipped"]}
    assert alpha["id"] in reasons
    assert 999999 in reasons

    detail = client.get(f"/api/admin/skill-bundles/{bundle['id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["member_count"] == 2
    assert len(detail.json()["members"]) == 2

    again = client.post(
        f"/api/admin/skill-bundles/{bundle['id']}/members",
        headers=auth_headers,
        json={"skill_ids": [alpha["id"]]},
    )
    assert again.json()["added"] == 0

    removed = client.delete(
        f"/api/admin/skill-bundles/{bundle['id']}/members/{beta['id']}",
        headers=auth_headers,
    )
    assert removed.status_code == 200
    detail = client.get(f"/api/admin/skill-bundles/{bundle['id']}", headers=auth_headers)
    assert detail.json()["member_count"] == 1

    patched = client.patch(
        f"/api/admin/skill-bundles/{bundle['id']}",
        headers=auth_headers,
        json={"name": "办公套件 Pro", "description": "更新说明"},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "办公套件 Pro"

    skill_id = alpha["id"]
    deleted = client.delete(f"/api/admin/skill-bundles/{bundle['id']}", headers=auth_headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/admin/skill-bundles/{bundle['id']}", headers=auth_headers).status_code == 404
    assert client.get(f"/api/admin/skills/{skill_id}", headers=auth_headers).status_code == 200


def test_bundle_zip_download_structure(client: TestClient, auth_headers: dict[str, str]) -> None:
    alpha = _upload_skill(client, auth_headers, "pack-alpha", SKILL_MD_A)
    beta = _upload_skill(client, auth_headers, "pack-beta", SKILL_MD_B)
    created = client.post("/api/admin/skill-bundles", headers=auth_headers, json={"name": "双包"})
    bundle_id = created.json()["id"]
    client.post(
        f"/api/admin/skill-bundles/{bundle_id}/members",
        headers=auth_headers,
        json={"skill_ids": [alpha["id"], beta["id"]]},
    )

    empty = client.post("/api/admin/skill-bundles", headers=auth_headers, json={"name": "空包"})
    empty_id = empty.json()["id"]
    assert client.get(f"/api/admin/skill-bundles/{empty_id}/download", headers=auth_headers).status_code == 400
    assert client.post(f"/api/admin/skill-bundles/{empty_id}/download-url", headers=auth_headers).status_code == 400

    download = client.get(f"/api/admin/skill-bundles/{bundle_id}/download", headers=auth_headers)
    assert download.status_code == 200, download.text
    assert download.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        names = archive.namelist()
    assert "pack-alpha/SKILL.md" in names
    assert "pack-beta/SKILL.md" in names
    assert all("/" in name for name in names)
    assert not any(name.startswith("双包/") for name in names)


def test_bundle_download_token(client: TestClient, auth_headers: dict[str, str]) -> None:
    alpha = _upload_skill(client, auth_headers, "pack-alpha", SKILL_MD_A)
    created = client.post("/api/admin/skill-bundles", headers=auth_headers, json={"name": "token-pack"})
    bundle_id = created.json()["id"]
    client.post(
        f"/api/admin/skill-bundles/{bundle_id}/members",
        headers=auth_headers,
        json={"skill_ids": [alpha["id"]]},
    )

    assert client.post(f"/api/admin/skill-bundles/{bundle_id}/download-url").status_code == 401
    issued = client.post(f"/api/admin/skill-bundles/{bundle_id}/download-url", headers=auth_headers)
    assert issued.status_code == 200, issued.text
    body = issued.json()
    assert body["expiresInSeconds"] == 300
    assert body["url"].startswith(f"/api/skill-bundles/{bundle_id}/download?token=")

    public = client.get(body["url"])
    assert public.status_code == 200, public.text
    with zipfile.ZipFile(io.BytesIO(public.content)) as archive:
        assert "pack-alpha/SKILL.md" in archive.namelist()

    settings = get_settings()
    now = datetime.now(timezone.utc)

    def make_token(*, expires_at, version: int, scope: str = "skill_bundle", bid: int = bundle_id) -> str:
        return jwt.encode(
            {"scope": scope, "bundle_id": bid, "sub": "admin", "ver": version, "iat": now, "exp": expires_at},
            settings.app_secret_key,
            algorithm="HS256",
        )

    expired = make_token(expires_at=now - timedelta(minutes=1), version=0)
    assert client.get(f"/api/skill-bundles/{bundle_id}/download?token={expired}").status_code == 401

    wrong_scope = make_token(expires_at=now + timedelta(minutes=5), version=0, scope="skill")
    assert client.get(f"/api/skill-bundles/{bundle_id}/download?token={wrong_scope}").status_code == 403

    wrong_id = make_token(expires_at=now + timedelta(minutes=5), version=0, bid=bundle_id + 99)
    assert client.get(f"/api/skill-bundles/{bundle_id}/download?token={wrong_id}").status_code == 403

    valid = make_token(expires_at=now + timedelta(minutes=5), version=0)
    assert client.get(f"/api/skill-bundles/{bundle_id}/download?token={valid}").status_code == 200

    with get_session_factory()() as db:
        admin = db.scalar(select(Admin).where(Admin.username == "admin"))
        assert admin is not None
        admin.token_version = int(admin.token_version or 0) + 1
        db.commit()
    assert client.get(f"/api/skill-bundles/{bundle_id}/download?token={valid}").status_code == 401


def test_bundle_missing_skill_member(client: TestClient, auth_headers: dict[str, str]) -> None:
    alpha = _upload_skill(client, auth_headers, "pack-alpha", SKILL_MD_A)
    beta = _upload_skill(client, auth_headers, "pack-beta", SKILL_MD_B)
    created = client.post("/api/admin/skill-bundles", headers=auth_headers, json={"name": "残包"})
    bundle_id = created.json()["id"]
    client.post(
        f"/api/admin/skill-bundles/{bundle_id}/members",
        headers=auth_headers,
        json={"skill_ids": [alpha["id"], beta["id"]]},
    )

    assert client.delete(f"/api/admin/skills/{beta['id']}", headers=auth_headers).status_code == 200
    detail = client.get(f"/api/admin/skill-bundles/{bundle_id}", headers=auth_headers)
    assert detail.status_code == 200
    members = detail.json()["members"]
    assert len(members) == 2
    missing = [item for item in members if item["missing"]]
    assert len(missing) == 1
    assert missing[0]["skill_id"] == beta["id"]
    assert missing[0]["skill"] is None

    download = client.get(f"/api/admin/skill-bundles/{bundle_id}/download", headers=auth_headers)
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        names = archive.namelist()
    assert "pack-alpha/SKILL.md" in names
    assert "pack-beta/SKILL.md" not in names

    with get_session_factory()() as db:
        assert db.get(Skill, alpha["id"]) is not None

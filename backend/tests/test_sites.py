from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.capabilities.site import sites as service
from app.capabilities.site import storage
from app.config import get_settings
from app.db import get_session_factory


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def site_zip(body: str = "home", asset: bytes = b"console.log(1)") -> bytes:
    return make_zip(
        {
            "index.html": body.encode("utf-8"),
            "assets/app.js": asset,
        }
    )


def deploy_admin(
    client: TestClient,
    auth_headers: dict[str, str],
    data: bytes,
    *,
    slug: str | None = None,
    name: str | None = None,
    entry: str | None = None,
):
    form = {}
    if slug:
        form["slug"] = slug
    if name:
        form["name"] = name
    if entry:
        form["entry"] = entry
    return client.post(
        "/api/admin/mcp/sites",
        headers=auth_headers,
        data=form,
        files={"file": ("site.zip", data, "application/zip")},
    )


def make_site_key(client: TestClient, auth_headers: dict[str, str], capabilities: list[str]) -> str:
    created = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "site-key", "capability_ids": capabilities},
    )
    assert created.status_code == 201, created.text
    return created.json()["key"]


def test_admin_deploy_and_preview(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = deploy_admin(client, auth_headers, site_zip("<h1>首页</h1>"), slug="demo", name="演示站")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["version"]["status"] == "unpacking"

    detail = client.get(f"/api/admin/mcp/sites/{body['site']['id']}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["slug"] == "demo"
    assert payload["versions"][0]["status"] == "ready"
    assert payload["versions"][0]["is_current"] is True
    assert payload["current_version_no"] == 1

    preview = client.get("/sites/demo/")
    assert preview.status_code == 200, preview.text
    assert "首页" in preview.text
    assert preview.headers["content-type"].startswith("text/html")

    asset = client.get("/sites/demo/assets/app.js")
    assert asset.status_code == 200
    assert "console.log" in asset.text
    assert asset.headers["x-content-type-options"] == "nosniff"

    redirect = client.get("/sites/demo", follow_redirects=False)
    assert redirect.status_code == 308
    assert redirect.headers["location"].endswith("/sites/demo/")


def test_absolute_asset_paths_are_rewritten(client: TestClient, auth_headers: dict[str, str]) -> None:
    html = (
        b"<!doctype html><html><head><title>t</title></head><body><div id=\"root\"></div>"
        b'<script type="module" src="/assets/index-abc.js"></script>'
        b'<link rel="stylesheet" href="/assets/index-abc.css">'
        b"</body></html>"
    )
    data = make_zip(
        {
            "index.html": html,
            "assets/index-abc.js": b"window.__site__=1;",
            "assets/index-abc.css": b"body{background:url(/assets/bg.png)}",
            "assets/bg.png": b"PNG",
        }
    )
    response = deploy_admin(client, auth_headers, data, slug="rewrite")
    assert response.status_code == 202, response.text

    page = client.get("/sites/rewrite/")
    assert page.status_code == 200, page.text
    assert 'src="/sites/rewrite/assets/index-abc.js"' in page.text
    assert 'href="/sites/rewrite/assets/index-abc.css"' in page.text
    assert '<base href="/sites/rewrite/">' in page.text
    assert 'src="/assets/index-abc.js"' not in page.text
    assert "boot-splash" not in page.text

    script = client.get("/sites/rewrite/assets/index-abc.js")
    assert script.status_code == 200
    assert b"__site__" in script.content

    css = client.get("/sites/rewrite/assets/index-abc.css")
    assert css.status_code == 200
    assert "url(/sites/rewrite/assets/bg.png)" in css.text


def test_html_in_subdirectory_uses_directory_as_base(client: TestClient, auth_headers: dict[str, str]) -> None:
    data = make_zip(
        {
            "index.html": b"<html><head></head><body>root</body></html>",
            "docs/guide.html": b'<html><head></head><body><img src="./pic.png"></body></html>',
            "docs/pic.png": b"PNG",
        }
    )
    deploy_admin(client, auth_headers, data, slug="subdir")
    page = client.get("/sites/subdir/docs/guide.html")
    assert page.status_code == 200, page.text
    assert '<base href="/sites/subdir/docs/">' in page.text


def test_top_level_directory_is_normalized(client: TestClient, auth_headers: dict[str, str]) -> None:
    data = make_zip({"dist/index.html": b"<p>dist</p>", "dist/main.js": b"run()"})
    response = deploy_admin(client, auth_headers, data, slug="dist-site")
    assert response.status_code == 202
    detail = client.get(f"/api/admin/mcp/sites/{response.json()['site']['id']}", headers=auth_headers).json()
    assert detail["versions"][0]["status"] == "ready"
    assert detail["versions"][0]["entry_file"] == "index.html"
    assert client.get("/sites/dist-site/main.js").status_code == 200


def test_spa_fallback_serves_entry(client: TestClient, auth_headers: dict[str, str]) -> None:
    deploy_admin(client, auth_headers, site_zip("<div id=app></div>"), slug="spa")
    response = client.get("/sites/spa/dashboard/settings")
    assert response.status_code == 200
    assert "app" in response.text
    missing = client.get("/sites/spa/missing.png")
    assert missing.status_code == 404


def test_unsafe_archive_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    data = make_zip({"index.html": b"ok", "../evil.txt": b"pwn"})
    response = deploy_admin(client, auth_headers, data, slug="unsafe")
    assert response.status_code == 202
    detail = client.get(f"/api/admin/mcp/sites/{response.json()['site']['id']}", headers=auth_headers).json()
    version = detail["versions"][0]
    assert version["status"] == "failed"
    assert detail["current_version_no"] is None
    assert client.get("/sites/unsafe/").status_code == 503


def test_version_rollback_and_current_protection(client: TestClient, auth_headers: dict[str, str]) -> None:
    first = deploy_admin(client, auth_headers, site_zip("v1-body"), slug="ver")
    site_id = first.json()["site"]["id"]
    second = client.post(
        f"/api/admin/mcp/sites/{site_id}/versions",
        headers=auth_headers,
        files={"file": ("site.zip", site_zip("v2-body"), "application/zip")},
    )
    assert second.status_code == 202, second.text
    assert "v2-body" in client.get("/sites/ver/").text

    rollback = client.post(
        f"/api/admin/mcp/sites/{site_id}/versions/{first.json()['version']['id']}/activate",
        headers=auth_headers,
    )
    assert rollback.status_code == 200, rollback.text
    assert "v1-body" in client.get("/sites/ver/").text

    current_id = first.json()["version"]["id"]
    removed = client.delete(
        f"/api/admin/mcp/sites/{site_id}/versions/{current_id}", headers=auth_headers
    )
    assert removed.status_code == 409


def test_duplicate_content_is_reused(client: TestClient, auth_headers: dict[str, str]) -> None:
    data = site_zip("same")
    first = deploy_admin(client, auth_headers, data, slug="dup")
    site_id = first.json()["site"]["id"]
    second = client.post(
        f"/api/admin/mcp/sites/{site_id}/versions",
        headers=auth_headers,
        files={"file": ("site.zip", data, "application/zip")},
    )
    assert second.status_code == 202, second.text
    detail = client.get(f"/api/admin/mcp/sites/{site_id}", headers=auth_headers).json()
    latest = detail["versions"][0]
    assert latest["status"] == "duplicate"
    assert latest["reused_version_id"] == first.json()["version"]["id"]
    assert not storage.version_dir(site_id, latest["version_no"]).exists()
    assert not storage.upload_path(latest["id"]).exists()


def test_token_access_and_reset(client: TestClient, auth_headers: dict[str, str]) -> None:
    deployed = deploy_admin(client, auth_headers, site_zip("secret"), slug="locked")
    site_id = deployed.json()["site"]["id"]
    token = client.post(f"/api/admin/mcp/sites/{site_id}/token", headers=auth_headers).json()["token"]

    blocked = client.get("/sites/locked/")
    assert blocked.status_code == 401
    assert "text/html" in blocked.headers["content-type"]

    allowed = client.get("/sites/locked/", params={"token": token})
    assert allowed.status_code == 200
    assert "secret" in allowed.text
    cookie = allowed.cookies.get(f"site_gate_{site_id}")
    assert cookie

    client.cookies.set(f"site_gate_{site_id}", cookie)
    assert client.get("/sites/locked/").status_code == 200

    client.post(f"/api/admin/mcp/sites/{site_id}/token", headers=auth_headers)
    assert client.get("/sites/locked/").status_code == 401


def test_protocol_rest_and_authorization(client: TestClient, auth_headers: dict[str, str]) -> None:
    allowed_key = make_site_key(client, auth_headers, ["site"])
    created = client.post(
        "/v1/sites",
        headers={"Authorization": f"Bearer {allowed_key}"},
        data={"slug": "rest-site", "name": "REST"},
        files={"file": ("site.zip", site_zip("rest-body"), "application/zip")},
    )
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["preview_url"].endswith("/sites/rest-site/")

    listed = client.get("/v1/sites", headers={"Authorization": f"Bearer {allowed_key}"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    denied_key = make_site_key(client, auth_headers, ["knowledge"])
    denied = client.get("/v1/sites", headers={"Authorization": f"Bearer {denied_key}"})
    assert denied.status_code == 403

    assert client.get("/v1/sites", headers={"Authorization": "Bearer sk-nope"}).status_code == 401

    stranger_key = make_site_key(client, auth_headers, ["site"])
    foreign = client.get("/v1/sites/rest-site", headers={"Authorization": f"Bearer {stranger_key}"})
    assert foreign.status_code == 404


def test_mcp_tool_deploy_and_gating(client: TestClient, auth_headers: dict[str, str]) -> None:
    import asyncio

    from app.capabilities import ensure_defaults, invoke_tool
    from app.services.mcp_auth import resolve_mcp_key

    allowed_key = make_site_key(client, auth_headers, ["site"])
    denied_key = make_site_key(client, auth_headers, ["knowledge"])
    session = get_session_factory()()
    ensure_defaults()
    try:
        owner = resolve_mcp_key(session, allowed_key)
        assert owner is not None
        payload = {
            "filename": "site.zip",
            "archive_base64": base64.b64encode(site_zip("mcp-body")).decode(),
            "slug": "mcp-site",
        }
        result = asyncio.run(invoke_tool(session, mcp_key=owner, tool_name="site_deploy", payload=payload))
        assert result["version"]["status"] == "ready"
        session.commit()
        assert "mcp-body" in client.get("/sites/mcp-site/").text

        denied = resolve_mcp_key(session, denied_key)
        assert denied is not None
        try:
            asyncio.run(invoke_tool(session, mcp_key=denied, tool_name="site_deploy", payload=payload))
            raise AssertionError("未授权 Key 不应调用成功")
        except Exception as error:  # noqa: BLE001
            assert getattr(error, "status_code", None) == 403
    finally:
        session.close()


def test_retention_purges_old_versions(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    first = deploy_admin(client, auth_headers, site_zip("r1"), slug="retain")
    site_id = first.json()["site"]["id"]
    for body in ("r2", "r3"):
        client.post(
            f"/api/admin/mcp/sites/{site_id}/versions",
            headers=auth_headers,
            files={"file": ("site.zip", site_zip(body), "application/zip")},
        )
    settings = get_settings()
    monkeypatch.setattr(settings, "site_max_versions", 1)

    session = get_session_factory()()
    try:
        removed = service.cleanup_expired(session)
        session.commit()
    finally:
        session.close()
    assert removed >= 2
    detail = client.get(f"/api/admin/mcp/sites/{site_id}", headers=auth_headers).json()
    purged = [item for item in detail["versions"] if item["purged"]]
    assert len(purged) == 2
    assert detail["versions"][0]["is_current"] is True
    assert "r3" in client.get("/sites/retain/").text


def test_reconcile_marks_stuck_versions_failed(client: TestClient, auth_headers: dict[str, str]) -> None:
    from app.models import Site, SiteVersion

    session = get_session_factory()()
    try:
        site = Site(id=service.new_id(), slug="stuck", name="stuck", entry_file="index.html", status="active")
        version = SiteVersion(
            id=service.new_id(),
            site_id=site.id,
            version_no=1,
            status="unpacking",
            stage="stored",
            entry_file="index.html",
        )
        session.add(site)
        session.add(version)
        session.commit()
        changed = service.reconcile_stuck(session)
        session.commit()
        assert changed == 1
        assert session.get(SiteVersion, version.id).status == "failed"
    finally:
        session.close()


def test_site_spec_registered() -> None:
    from app.capabilities import ensure_defaults, get_provider

    ensure_defaults()
    provider = get_provider("site")
    assert provider is not None
    assert provider.spec.admin_path == "/mcp-plaza/sites"
    assert provider.spec.icon == "globe"
    tool_names = {tool.name for tool in provider.list_mcp_tools()}
    assert {"site_deploy", "site_list", "site_status", "site_rollback", "site_delete", "site_access"} <= tool_names


def test_protocol_access_and_token_reset(client: TestClient, auth_headers: dict[str, str]) -> None:
    allowed_key = make_site_key(client, auth_headers, ["site"])
    headers = {"Authorization": f"Bearer {allowed_key}"}
    created = client.post(
        "/v1/sites",
        headers=headers,
        data={"slug": "access-site"},
        files={"file": ("site.zip", site_zip("guarded"), "application/zip")},
    )
    assert created.status_code == 202, created.text

    opened = client.post("/v1/sites/access-site/access", headers=headers, json={"mode": "token"})
    assert opened.status_code == 200, opened.text
    token = opened.json()["token"]
    assert token and token.startswith("st-")
    assert opened.json()["site"]["access_mode"] == "token"
    assert client.get("/sites/access-site/").status_code == 401
    assert client.get("/sites/access-site/", params={"token": token}).status_code == 200

    reopened = client.post("/v1/sites/access-site/access", headers=headers, json={"mode": "public"})
    assert reopened.status_code == 200
    assert reopened.json()["token"] is None
    assert client.get("/sites/access-site/").status_code == 200

    client.post("/v1/sites/access-site/access", headers=headers, json={"mode": "token"})
    reset = client.post("/v1/sites/access-site/token", headers=headers)
    assert reset.status_code == 200
    new_token = reset.json()["token"]
    assert new_token and new_token != token
    assert client.get("/sites/access-site/", params={"token": token}).status_code == 401
    assert client.get("/sites/access-site/", params={"token": new_token}).status_code == 200


def test_mcp_access_tool(client: TestClient, auth_headers: dict[str, str]) -> None:
    import asyncio

    from app.capabilities import ensure_defaults, invoke_tool
    from app.services.mcp_auth import resolve_mcp_key

    key = make_site_key(client, auth_headers, ["site"])
    session = get_session_factory()()
    ensure_defaults()
    try:
        owner = resolve_mcp_key(session, key)
        assert owner is not None
        deploy = asyncio.run(
            invoke_tool(
                session,
                mcp_key=owner,
                tool_name="site_deploy",
                payload={
                    "filename": "site.zip",
                    "archive_base64": base64.b64encode(site_zip("mcp-guarded")).decode(),
                    "slug": "mcp-access",
                },
            )
        )
        assert deploy["version"]["status"] == "ready"

        result = asyncio.run(
            invoke_tool(
                session,
                mcp_key=owner,
                tool_name="site_access",
                payload={"slug": "mcp-access", "mode": "token"},
            )
        )
        session.commit()
        assert result["site"]["access_mode"] == "token"
        assert result["token"].startswith("st-")
        assert client.get("/sites/mcp-access/", params={"token": result["token"]}).status_code == 200
    finally:
        session.close()


def test_cleanup_temp_ignores_upload_in_use(client: TestClient, auth_headers: dict[str, str]) -> None:
    deployed = deploy_admin(client, auth_headers, site_zip("temp"), slug="temp-site")
    assert deployed.status_code == 202
    root: Path = get_settings().resolved_site_deploy_path
    assert root.exists()

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


def test_comment_mentioning_base_tag_still_injects_base(client: TestClient, auth_headers: dict[str, str]) -> None:
    """源码注释里出现 `<base href>` 不能阻止注入，否则相对链接会退回文档基准 URL。"""
    html = (
        '<html><head><!-- 依赖注入的 <base href> 兜底 --><link href="/assets/app.css"></head>'
        "<body>doc</body></html>"
    )
    data = make_zip({"index.html": html.encode("utf-8"), "assets/app.css": b"body{}"})
    deploy_admin(client, auth_headers, data, slug="base-comment")
    page = client.get("/sites/base-comment/")
    assert page.status_code == 200, page.text
    assert '<base href="/sites/base-comment/">' in page.text
    assert 'href="/sites/base-comment/assets/app.css"' in page.text


def test_existing_base_tag_is_not_duplicated(client: TestClient, auth_headers: dict[str, str]) -> None:
    data = make_zip({"index.html": b'<html><head><base href="/custom/"></head><body>x</body></html>'})
    deploy_admin(client, auth_headers, data, slug="own-base")
    page = client.get("/sites/own-base/")
    assert page.status_code == 200, page.text
    assert page.text.count("<base ") == 1


def test_admin_edit_site_metadata(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = deploy_admin(client, auth_headers, site_zip("<p>edit</p>"), slug="edit-me")
    site_id = created.json()["site"]["id"]

    response = client.patch(
        f"/api/admin/mcp/sites/{site_id}",
        headers=auth_headers,
        json={"name": "新名字", "description": "站点用途说明"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["name"] == "新名字"
    assert payload["description"] == "站点用途说明"

    # 未提交的字段不能被顺手改掉
    assert payload["slug"] == "edit-me"
    assert client.get("/sites/edit-me/").status_code == 200


def test_admin_edit_can_clear_description_and_reset_entry(client: TestClient, auth_headers: dict[str, str]) -> None:
    """空串必须被当作「显式清空」，不能被真值判断静默忽略。"""
    created = deploy_admin(client, auth_headers, site_zip("<p>clear</p>"), slug="clear-me")
    site_id = created.json()["site"]["id"]

    client.patch(
        f"/api/admin/mcp/sites/{site_id}",
        headers=auth_headers,
        json={"description": "待清空", "entry_file": "other.html"},
    )
    cleared = client.patch(
        f"/api/admin/mcp/sites/{site_id}",
        headers=auth_headers,
        json={"description": "", "entry_file": ""},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["description"] == ""
    assert cleared.json()["entry_file"] == "index.html"


def test_admin_edit_slug_moves_preview_url(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = deploy_admin(client, auth_headers, site_zip("<p>move</p>"), slug="old-slug")
    site_id = created.json()["site"]["id"]
    assert client.get("/sites/old-slug/").status_code == 200

    response = client.patch(f"/api/admin/mcp/sites/{site_id}", headers=auth_headers, json={"slug": "new-slug"})
    assert response.status_code == 200, response.text
    assert response.json()["slug"] == "new-slug"
    assert client.get("/sites/new-slug/").status_code == 200
    assert client.get("/sites/old-slug/").status_code == 404


def test_admin_edit_slug_rejects_duplicate_and_invalid(client: TestClient, auth_headers: dict[str, str]) -> None:
    first = deploy_admin(client, auth_headers, site_zip("a"), slug="taken-slug")
    second = deploy_admin(client, auth_headers, site_zip("b"), slug="other-slug")
    first_id = first.json()["site"]["id"]
    second_id = second.json()["site"]["id"]

    conflict = client.patch(f"/api/admin/mcp/sites/{second_id}", headers=auth_headers, json={"slug": "taken-slug"})
    assert conflict.status_code == 409, conflict.text

    invalid = client.patch(f"/api/admin/mcp/sites/{first_id}", headers=auth_headers, json={"slug": "Bad Slug"})
    assert invalid.status_code == 400, invalid.text

    # 失败不应改动任何数据
    assert client.get("/sites/taken-slug/").status_code == 200
    assert client.get("/sites/other-slug/").status_code == 200


def test_admin_edit_rejects_bad_enum_values(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = deploy_admin(client, auth_headers, site_zip("enum"), slug="enum-site")
    site_id = created.json()["site"]["id"]
    for payload in ({"status": "paused"}, {"access_mode": "secret"}):
        response = client.patch(f"/api/admin/mcp/sites/{site_id}", headers=auth_headers, json=payload)
        assert response.status_code == 400, response.text


def test_v1_edit_requires_ownership_and_site_capability(client: TestClient, auth_headers: dict[str, str]) -> None:
    deployed = deploy_admin(client, auth_headers, site_zip("owned"), slug="owned-site")
    owner_key = make_site_key(client, auth_headers, ["site"])
    other_key = make_site_key(client, auth_headers, ["site"])
    denied_key = make_site_key(client, auth_headers, ["knowledge"])

    # 管理员部署（mcp_key_id 为空）的站点不属于任何 Key，Key 编辑应视为不存在
    assert (
        client.patch(
            "/v1/sites/owned-site",
            headers={"Authorization": f"Bearer {owner_key}"},
            json={"name": "偷改"},
        ).status_code
        == 404
    )
    assert (
        client.patch(
            "/v1/sites/owned-site",
            headers={"Authorization": f"Bearer {denied_key}"},
            json={"name": "无权"},
        ).status_code
        == 403
    )

    # Key 自己部署的站点可以编辑
    client.post(
        "/v1/sites",
        headers={"Authorization": f"Bearer {owner_key}"},
        data={"slug": "key-site"},
        files={"file": ("site.zip", site_zip("key-site"), "application/zip")},
    )
    edited = client.patch(
        "/v1/sites/key-site",
        headers={"Authorization": f"Bearer {owner_key}"},
        json={"name": "Key 改的", "spa_fallback": False},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "Key 改的"
    assert edited.json()["spa_fallback"] is False
    assert deployed.json()["site"]["id"]


def test_mcp_site_update_tool_edits_site(client: TestClient, auth_headers: dict[str, str]) -> None:
    import asyncio

    from app.capabilities import ensure_defaults, invoke_tool
    from app.services.mcp_auth import resolve_mcp_key

    key = make_site_key(client, auth_headers, ["site"])
    payload = {
        "filename": "site.zip",
        "archive_base64": base64.b64encode(site_zip("mcp-edit")).decode(),
        "slug": "mcp-edit-site",
    }
    session = get_session_factory()()
    ensure_defaults()
    try:
        owner = resolve_mcp_key(session, key)
        assert owner is not None
        created = asyncio.run(invoke_tool(session, mcp_key=owner, tool_name="site_deploy", payload=payload))
        assert created["version"]["status"] == "ready"
        session.commit()

        result = asyncio.run(
            invoke_tool(
                session,
                mcp_key=owner,
                tool_name="site_update",
                payload={
                    "slug": "mcp-edit-site",
                    "new_slug": "mcp-renamed",
                    "name": "改名了",
                    "description": "由 MCP 工具编辑",
                    "spa_fallback": False,
                },
            )
        )
        session.commit()
        assert result["slug"] == "mcp-renamed"
        assert result["name"] == "改名了"
        assert result["description"] == "由 MCP 工具编辑"
        assert result["spa_fallback"] is False
        assert "mcp-edit" in client.get("/sites/mcp-renamed/").text
    finally:
        session.close()


def test_html_is_served_with_no_store(client: TestClient, auth_headers: dict[str, str]) -> None:
    """入口 HTML 不能留浏览器副本，否则新版本部署后仍引用已删除的哈希资源。"""
    data = make_zip(
        {
            "index.html": b"<html><head></head><body>entry</body></html>",
            "docs/guide.html": b"<html><head></head><body>guide</body></html>",
            "assets/app.js": b"console.log(1)",
            "assets/index-a1b2c3d4.js": b"console.log(2)",
        }
    )
    deploy_admin(client, auth_headers, data, slug="nocache-html")

    for path in ("/sites/nocache-html/", "/sites/nocache-html/index.html", "/sites/nocache-html/docs/guide.html"):
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store", path


def test_only_hashed_assets_are_immutable(client: TestClient, auth_headers: dict[str, str]) -> None:
    """文件名不带内容哈希就不能强缓存，否则换版本后旧文件一年内不生效。"""
    data = make_zip(
        {
            "index.html": b"<html><head></head><body>x</body></html>",
            "assets/index-a1b2c3d4.js": b"console.log(1)",
            "assets/logo.png": b"PNG",
        }
    )
    deploy_admin(client, auth_headers, data, slug="hashed-assets")

    hashed = client.get("/sites/hashed-assets/assets/index-a1b2c3d4.js")
    assert hashed.status_code == 200
    # 站点资源封顶三天，不复用管理端的一年
    assert hashed.headers["cache-control"] == "public, max-age=259200, immutable"

    mutable = client.get("/sites/hashed-assets/assets/logo.png")
    assert mutable.status_code == 200
    assert mutable.headers["cache-control"] == "public, max-age=86400"


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
    assert {
        "site_deploy",
        "site_list",
        "site_status",
        "site_rollback",
        "site_delete",
        "site_access",
        "site_access_info",
    } <= tool_names
    deploy = next(tool for tool in provider.list_mcp_tools() if tool.name == "site_deploy")
    assert "slug" in (deploy.input_schema.get("required") or [])
    assert "语义化" in deploy.description


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
    assert opened.json()["url"].endswith(f"/sites/access-site/?token={token}")
    assert client.get("/sites/access-site/").status_code == 401
    assert client.get("/sites/access-site/", params={"token": token}).status_code == 200

    info = client.get("/v1/sites/access-site/access", headers=headers)
    assert info.status_code == 200, info.text
    assert info.json()["token"] == token
    assert info.json()["url"].endswith(f"?token={token}")

    reopened = client.post("/v1/sites/access-site/access", headers=headers, json={"mode": "public"})
    assert reopened.status_code == 200
    assert reopened.json()["token"] is None
    assert client.get("/sites/access-site/").status_code == 200

    switched = client.post("/v1/sites/access-site/access", headers=headers, json={"mode": "token"})
    assert switched.status_code == 200, switched.text
    assert switched.json()["token"] == token
    assert switched.json()["url"].endswith(f"?token={token}")

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
        assert result["url"].endswith(f"?token={result['token']}")
        assert client.get("/sites/mcp-access/", params={"token": result["token"]}).status_code == 200

        info = asyncio.run(
            invoke_tool(
                session,
                mcp_key=owner,
                tool_name="site_access_info",
                payload={"slug": "mcp-access"},
            )
        )
        assert info["token"] == result["token"]
        assert info["url"].endswith(f"?token={result['token']}")
    finally:
        session.close()


def test_cleanup_temp_ignores_upload_in_use(client: TestClient, auth_headers: dict[str, str]) -> None:
    deployed = deploy_admin(client, auth_headers, site_zip("temp"), slug="temp-site")
    assert deployed.status_code == 202
    root: Path = get_settings().resolved_site_deploy_path
    assert root.exists()

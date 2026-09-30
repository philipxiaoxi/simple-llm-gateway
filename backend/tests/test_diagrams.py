from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.capabilities.diagram import renderer
from app.capabilities.diagram.errors import DiagramError
from app.config import get_settings
from app.db import get_session_factory

FIXTURE = Path(__file__).parent / "fixtures" / "diagram_architecture.json"


def load_source(title: str = "RAG Pipeline") -> dict:
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    source["meta"]["title"] = title
    return source


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def fake_renderer(monkeypatch, *, returncode: int = 0, diagnostics: list[dict] | None = None):
    """把渲染子进程替换为写固定 HTML 的桩，保留源标题便于断言版本切换。"""

    def fake_run(diagram_type, source_path, out_path, quality, sandbox):
        data = json.loads(source_path.read_text(encoding="utf-8"))
        title = str(data.get("meta", {}).get("title", ""))
        if returncode == 0:
            out_path.write_text(
                f"<!doctype html><html><body>DIAGRAM:{title}</body></html>", encoding="utf-8"
            )
        stdout = json.dumps({"ok": returncode == 0, "diagnostics": diagnostics or []})
        return returncode, stdout, ""

    monkeypatch.setattr(renderer, "_run_cli", fake_run)


def make_key(client: TestClient, auth_headers: dict[str, str], capabilities: list[str]) -> str:
    created = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "diagram-key", "capability_ids": capabilities},
    )
    assert created.status_code == 201, created.text
    return created.json()["key"]


def deploy_admin_site(client: TestClient, auth_headers: dict[str, str], slug: str) -> None:
    data = make_zip({"index.html": b"<h1>upload</h1>"})
    response = client.post(
        "/api/admin/mcp/sites",
        headers=auth_headers,
        data={"slug": slug},
        files={"file": ("site.zip", data, "application/zip")},
    )
    assert response.status_code == 202, response.text


def rest_create(
    client: TestClient,
    key: str,
    source: dict,
    *,
    slug: str = "diagram",
    diagram_type: str = "architecture",
    quality: str = "showcase",
    activate: bool = True,
) -> "object":
    return client.post(
        "/v1/diagrams",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "type": diagram_type,
            "source": source,
            "slug": slug,
            "quality": quality,
            "activate": activate,
        },
    )


# --- renderer 单元 ---


def test_renderer_rejects_unknown_type() -> None:
    with pytest.raises(DiagramError) as excinfo:
        renderer.normalize_type("sankey")
    assert excinfo.value.error_type == "invalid_request"


def test_renderer_parses_diagnostics_from_stdout() -> None:
    diagnostics = [{"code": "output/meta-path-syntax", "message": "bad path"}]
    parsed = renderer._parse_diagnostics(json.dumps({"ok": False, "diagnostics": diagnostics}))
    assert parsed == diagnostics


def test_renderer_missing_binary_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "diagram_node_binary", "definitely-not-a-real-binary-xyz")
    import asyncio

    with pytest.raises(DiagramError) as excinfo:
        asyncio.run(renderer.render_diagram("architecture", load_source(), "showcase"))
    assert excinfo.value.status_code == 503
    assert excinfo.value.error_type == "renderer_unavailable"


def test_renderer_timeout_maps_to_504(monkeypatch) -> None:
    class FakeProc:
        pid = 2**16 + 1234
        returncode = None

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="archify", timeout=timeout)

        def wait(self):
            return 0

    monkeypatch.setattr(renderer.subprocess, "Popen", lambda *args, **kwargs: FakeProc())
    monkeypatch.setattr(renderer.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(renderer.os, "killpg", lambda *args, **kwargs: None)
    import asyncio

    with pytest.raises(DiagramError) as excinfo:
        asyncio.run(renderer.render_diagram("architecture", load_source(), "showcase"))
    assert excinfo.value.status_code == 504
    assert excinfo.value.error_type == "diagram_render_timeout"


def test_renderer_invalid_source_carries_diagnostics(monkeypatch) -> None:
    diagnostics = [{"code": "output/meta-path-syntax", "message": "meta.output must be portable"}]
    fake_renderer(monkeypatch, returncode=1, diagnostics=diagnostics)
    import asyncio

    with pytest.raises(DiagramError) as excinfo:
        asyncio.run(renderer.render_diagram("architecture", load_source(), "showcase"))
    assert excinfo.value.status_code == 400
    assert excinfo.value.error_type == "diagram_invalid"
    assert excinfo.value.diagnostics == diagnostics


@pytest.mark.skipif(
    shutil.which(get_settings().diagram_node_binary) is None or not renderer.cli_path().is_file(),
    reason="本机缺少 Node 或 vendored 渲染器，跳过真实渲染冒烟",
)
def test_renderer_real_smoke() -> None:
    import asyncio

    outcome = asyncio.run(renderer.render_diagram("architecture", load_source(), "showcase"))
    assert outcome.html.startswith(b"<!")
    text = outcome.html.decode("utf-8", "ignore")
    assert 'src="http' not in text and 'href="http' not in text


# --- REST 闭环 ---


def test_rest_create_renders_and_serves(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    fake_renderer(monkeypatch)
    key = make_key(client, auth_headers, ["diagram"])
    response = rest_create(client, key, load_source("闭环"))
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["preview_url"].endswith("/sites/diagram/")

    page = client.get("/sites/diagram/")
    assert page.status_code == 200, page.text
    assert "DIAGRAM:闭环" in page.text

    detail = client.get("/v1/diagrams/diagram", headers={"Authorization": f"Bearer {key}"})
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["origin"] == "diagram"
    assert payload["versions"][0]["diagram_type"] == "architecture"
    assert payload["versions"][0]["quality"] == "showcase"

    source = client.get("/v1/diagrams/diagram/source", headers={"Authorization": f"Bearer {key}"})
    assert source.status_code == 200, source.text
    assert source.json()["source"]["components"][0]["id"] == "user"


def test_rest_invalid_source_returns_diagnostics(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    diagnostics = [{"code": "output/meta-path-syntax", "message": "meta.output must be portable"}]
    fake_renderer(monkeypatch, returncode=1, diagnostics=diagnostics)
    key = make_key(client, auth_headers, ["diagram"])
    response = rest_create(client, key, load_source())
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["type"] == "diagram_invalid"
    assert error["diagnostics"] == diagnostics
    # 失败不应产生可访问站点
    assert client.get("/sites/diagram/").status_code == 404


def test_rest_rollback_switches_content(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    fake_renderer(monkeypatch)
    key = make_key(client, auth_headers, ["diagram"])
    headers = {"Authorization": f"Bearer {key}"}
    assert rest_create(client, key, load_source("v1")).status_code == 202
    assert rest_create(client, key, load_source("v2")).status_code == 202
    assert "DIAGRAM:v2" in client.get("/sites/diagram/").text

    detail = client.get("/v1/diagrams/diagram", headers=headers).json()
    assert [v["version_no"] for v in detail["versions"]][:2] == [2, 1]

    rollback = client.post("/v1/diagrams/diagram/rollback", headers=headers, json={"version_no": 1})
    assert rollback.status_code == 200, rollback.text
    assert "DIAGRAM:v1" in client.get("/sites/diagram/").text

    historic = client.get(
        "/v1/diagrams/diagram/source", headers=headers, params={"version_no": 1}
    ).json()
    assert historic["source"]["meta"]["title"] == "v1"


def test_rest_access_token_flow(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    fake_renderer(monkeypatch)
    key = make_key(client, auth_headers, ["diagram"])
    headers = {"Authorization": f"Bearer {key}"}
    assert rest_create(client, key, load_source("secret")).status_code == 202

    opened = client.post("/v1/diagrams/diagram/access", headers=headers, json={"mode": "token"})
    assert opened.status_code == 200, opened.text
    token = opened.json()["token"]
    assert token and token.startswith("st-")
    assert client.get("/sites/diagram/").status_code == 401
    assert client.get("/sites/diagram/", params={"token": token}).status_code == 200

    info = client.get("/v1/diagrams/diagram/access", headers=headers)
    assert info.status_code == 200
    assert info.json()["token"] == token

    reset = client.post("/v1/diagrams/diagram/token", headers=headers)
    new_token = reset.json()["token"]
    assert new_token != token
    assert client.get("/sites/diagram/", params={"token": token}).status_code == 401
    assert client.get("/sites/diagram/", params={"token": new_token}).status_code == 200


def test_rest_update_and_delete(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    fake_renderer(monkeypatch)
    key = make_key(client, auth_headers, ["diagram"])
    headers = {"Authorization": f"Bearer {key}"}
    assert rest_create(client, key, load_source()).status_code == 202

    edited = client.patch(
        "/v1/diagrams/diagram", headers=headers, json={"name": "改过的图表", "description": "说明"}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "改过的图表"

    removed = client.delete("/v1/diagrams/diagram", headers=headers)
    assert removed.status_code == 204
    assert client.get("/sites/diagram/").status_code == 404


# --- 隔离与授权 ---


def test_list_isolates_diagrams_and_sites(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    fake_renderer(monkeypatch)
    deploy_admin_site(client, auth_headers, "upload-one")
    key = make_key(client, auth_headers, ["diagram"])
    assert rest_create(client, key, load_source(), slug="diagram-one").status_code == 202

    diagrams = client.get("/v1/diagrams", headers={"Authorization": f"Bearer {key}"}).json()
    assert diagrams["total"] == 1
    assert diagrams["items"][0]["slug"] == "diagram-one"

    admin_diagrams = client.get("/api/admin/mcp/diagrams", headers=auth_headers).json()
    assert admin_diagrams["total"] == 1

    admin_sites = client.get("/api/admin/mcp/sites", headers=auth_headers).json()
    assert {item["slug"] for item in admin_sites["items"]} == {"upload-one"}


def test_rest_authorization_and_ownership(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    fake_renderer(monkeypatch)
    owner = make_key(client, auth_headers, ["diagram"])
    stranger = make_key(client, auth_headers, ["diagram"])
    denied = make_key(client, auth_headers, ["knowledge"])
    assert rest_create(client, owner, load_source()).status_code == 202

    assert (
        client.get("/v1/diagrams", headers={"Authorization": f"Bearer {denied}"}).status_code == 403
    )
    assert (
        client.get("/v1/diagrams/diagram", headers={"Authorization": f"Bearer {stranger}"}).status_code == 404
    )
    assert client.get("/v1/diagrams", headers={"Authorization": "Bearer sk-nope"}).status_code == 401


def test_public_source_requires_diagram_scope(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    fake_renderer(monkeypatch)
    site_key = make_key(client, auth_headers, ["site"])
    # 站点 Key 不能访问图表 REST
    assert client.get("/v1/diagrams", headers={"Authorization": f"Bearer {site_key}"}).status_code == 403


# --- MCP 工具 ---


def test_mcp_diagram_tools_roundtrip(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    import asyncio

    from app.capabilities import ensure_defaults, invoke_tool
    from app.services.mcp_auth import resolve_mcp_key

    fake_renderer(monkeypatch)
    key = make_key(client, auth_headers, ["diagram"])
    session = get_session_factory()()
    ensure_defaults()
    try:
        owner = resolve_mcp_key(session, key)
        assert owner is not None
        created = asyncio.run(
            invoke_tool(
                session,
                mcp_key=owner,
                tool_name="diagram_create",
                payload={"type": "architecture", "source": load_source("mcp"), "slug": "mcp-diagram"},
            )
        )
        session.commit()
        assert created["version"]["status"] == "ready"
        assert "DIAGRAM:mcp" in client.get("/sites/mcp-diagram/").text

        source = asyncio.run(
            invoke_tool(
                session,
                mcp_key=owner,
                tool_name="diagram_get_source",
                payload={"slug": "mcp-diagram"},
            )
        )
        assert source["type"] == "architecture"
        assert source["source"]["meta"]["title"] == "mcp"

        listed = asyncio.run(
            invoke_tool(session, mcp_key=owner, tool_name="diagram_list", payload={})
        )
        assert [item["slug"] for item in listed["items"]] == ["mcp-diagram"]

        updated = asyncio.run(
            invoke_tool(
                session,
                mcp_key=owner,
                tool_name="diagram_update",
                payload={"slug": "mcp-diagram", "new_slug": "mcp-renamed"},
            )
        )
        assert updated["slug"] == "mcp-renamed"

        access = asyncio.run(
            invoke_tool(
                session,
                mcp_key=owner,
                tool_name="diagram_access",
                payload={"slug": "mcp-renamed", "mode": "token"},
            )
        )
        assert access["token"].startswith("st-")

        deleted = asyncio.run(
            invoke_tool(
                session,
                mcp_key=owner,
                tool_name="diagram_delete",
                payload={"slug": "mcp-renamed"},
            )
        )
        assert deleted["deleted"] is True
    finally:
        session.close()


def test_mcp_diagram_create_rejects_invalid_source(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    import asyncio

    from app.capabilities import ensure_defaults, invoke_tool
    from app.capabilities.base import CapabilityError
    from app.services.mcp_auth import resolve_mcp_key

    diagnostics = [{"code": "output/meta-path-syntax", "message": "bad"}]
    fake_renderer(monkeypatch, returncode=1, diagnostics=diagnostics)
    key = make_key(client, auth_headers, ["diagram"])
    session = get_session_factory()()
    ensure_defaults()
    try:
        owner = resolve_mcp_key(session, key)
        assert owner is not None
        with pytest.raises(CapabilityError) as excinfo:
            asyncio.run(
                invoke_tool(
                    session,
                    mcp_key=owner,
                    tool_name="diagram_create",
                    payload={"type": "architecture", "source": load_source()},
                )
            )
        assert excinfo.value.error_type == "diagram_invalid"
        assert excinfo.value.diagnostics == diagnostics
    finally:
        session.close()


def test_mcp_diagram_requires_scope(client: TestClient, auth_headers: dict[str, str]) -> None:
    import asyncio

    from app.capabilities import ensure_defaults, invoke_tool
    from app.capabilities.base import CapabilityError
    from app.services.mcp_auth import resolve_mcp_key

    key = make_key(client, auth_headers, ["knowledge"])
    session = get_session_factory()()
    ensure_defaults()
    try:
        owner = resolve_mcp_key(session, key)
        assert owner is not None
        with pytest.raises(CapabilityError) as excinfo:
            asyncio.run(
                invoke_tool(session, mcp_key=owner, tool_name="diagram_list", payload={})
            )
        assert excinfo.value.status_code == 403
    finally:
        session.close()


def test_duplicate_source_reuses_version(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    fake_renderer(monkeypatch)
    key = make_key(client, auth_headers, ["diagram"])
    headers = {"Authorization": f"Bearer {key}"}
    first = rest_create(client, key, load_source("dup"))
    assert first.status_code == 202
    second = rest_create(client, key, load_source("dup"))
    assert second.status_code == 202

    detail = client.get("/v1/diagrams/diagram", headers=headers).json()
    latest = detail["versions"][0]
    assert latest["status"] == "duplicate"
    assert latest["reused_version_id"] == first.json()["version"]["id"]


def test_diagram_spec_registered() -> None:
    from app.capabilities import ensure_defaults, get_provider

    ensure_defaults()
    provider = get_provider("diagram")
    assert provider is not None
    assert provider.spec.admin_path == "/mcp-plaza/diagrams"
    assert provider.spec.icon == "shapes"
    tool_names = {tool.name for tool in provider.list_mcp_tools()}
    assert {
        "diagram_create",
        "diagram_list",
        "diagram_status",
        "diagram_get_source",
        "diagram_update",
        "diagram_rollback",
        "diagram_access",
        "diagram_access_info",
        "diagram_delete",
    } <= tool_names


def test_admin_diagram_create_and_source(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    fake_renderer(monkeypatch)
    created = client.post(
        "/api/admin/mcp/diagrams",
        headers=auth_headers,
        json={"type": "architecture", "source": load_source("admin"), "slug": "admin-diagram"},
    )
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["site"]["origin"] == "diagram"

    source = client.get(f"/api/admin/mcp/diagrams/{body['site']['id']}/source", headers=auth_headers)
    assert source.status_code == 200, source.text
    assert source.json()["source"]["meta"]["title"] == "admin"

    # 普通站点不能通过图表管理端点访问
    site_zip = make_zip({"index.html": b"upload"})
    deployed = client.post(
        "/api/admin/mcp/sites",
        headers=auth_headers,
        data={"slug": "not-diagram"},
        files={"file": ("site.zip", site_zip, "application/zip")},
    )
    site_id = deployed.json()["site"]["id"]
    assert client.get(f"/api/admin/mcp/diagrams/{site_id}", headers=auth_headers).status_code == 404

from __future__ import annotations

import base64
import io

from fastapi.testclient import TestClient

from app.config import get_settings


def _create_key(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    caps: tuple[str, ...] = ("info",),
    name: str = "reporter",
) -> str:
    response = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": name, "capability_ids": list(caps)},
    )
    assert response.status_code == 201, response.text
    return response.json()["key"]


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (6, 4), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_catalog_lists_info_capability(client: TestClient, auth_headers: dict[str, str]) -> None:
    catalog = client.get("/api/admin/mcp/catalog", headers=auth_headers).json()
    entry = next((row for row in catalog["items"] if row["capability_id"] == "info"), None)
    assert entry is not None
    assert entry["status"] == "enabled"
    paths = {row["path"] for row in entry["integration"]["rest_endpoints"]}
    assert "/v1/info/report" in paths
    names = {tool["name"] for tool in entry["tools"]}
    assert {"info_report", "info_report_batch", "info_report_config"} <= names


def test_report_requires_authorization(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.post("/v1/info/report", json={"text": "x"}).status_code == 401
    other = _create_key(client, auth_headers, caps=("knowledge",), name="kb-only")
    response = client.post("/v1/info/report", headers=_auth(other), json={"text": "x"})
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "permission_error"


def test_report_text_uses_normal_ai_visibility(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    key = _create_key(client, auth_headers)
    response = client.post(
        "/v1/info/report",
        headers=_auth(key),
        json={"text": "今日头条：测试资讯正文", "title": "T", "url": "https://example.com/a"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["duplicate"] is False
    assert body["is_featured"] is False
    assert body["media_count"] == 0
    item_id = body["id"]

    admin = client.get(f"/api/admin/info/items/{item_id}", headers=auth_headers)
    assert admin.status_code == 200, admin.text
    detail = admin.json()
    assert detail["author_name"]
    assert detail["ai_status"] == "pending"
    assert detail["is_featured"] is False

    # 未精选：公开页默认不展示，等 AI 判定后按常规上精选
    public = client.get("/api/public/info/items")
    assert public.status_code == 200
    assert all(row["id"] != item_id for row in public.json()["items"])


def test_report_media_multipart_stores_and_serves(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    key = _create_key(client, auth_headers)
    response = client.post(
        "/v1/info/report",
        headers=_auth(key),
        data={"text": "带图资讯"},
        files={"files": ("a.png", _png(), "image/png")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["media_count"] == 1
    item_dir = get_settings().resolved_info_media_path / body["id"]
    assert len(list(item_dir.glob("*.png"))) == 1

    detail = client.get(f"/api/admin/info/items/{body['id']}", headers=auth_headers).json()
    assert detail["media"] and detail["media"][0]["status"] == "ready"
    assert detail["media"][0]["url"]


def test_report_rejects_svg(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _create_key(client, auth_headers)
    response = client.post(
        "/v1/info/report",
        headers=_auth(key),
        data={"text": "svg"},
        files={"files": ("x.svg", b"<svg onload=alert(1)></svg>", "image/svg+xml")},
    )
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "unsupported_content_type"


def test_report_rejects_empty_content(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _create_key(client, auth_headers)
    response = client.post("/v1/info/report", headers=_auth(key), json={})
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request"


def test_report_media_base64_json(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _create_key(client, auth_headers)
    payload = {
        "text": "json media",
        "media": [{"filename": "a.png", "content_base64": base64.b64encode(_png()).decode()}],
    }
    response = client.post("/v1/info/report", headers=_auth(key), json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["media_count"] == 1


def test_report_dedup_by_external_id(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _create_key(client, auth_headers)
    payload = {"text": "重复内容唯一标记", "external_id": "news-1"}
    first = client.post("/v1/info/report", headers=_auth(key), json=payload).json()
    second = client.post("/v1/info/report", headers=_auth(key), json=payload).json()
    assert second["duplicate"] is True
    assert second["id"] == first["id"]
    listing = client.get("/api/admin/info/items?q=重复内容唯一标记", headers=auth_headers).json()
    assert listing["total"] == 1


def test_report_batch_partial(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _create_key(client, auth_headers)
    body = {
        "items": [
            {"text": "batch-1", "external_id": "b1"},
            {"text": "batch-1", "external_id": "b1"},
            {},
        ]
    }
    response = client.post("/v1/info/report/batch", headers=_auth(key), json=body)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["created"] == 1
    assert out["duplicates"] == 1
    assert out["failed"] == 1


def test_report_config_endpoint(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _create_key(client, auth_headers)
    response = client.get("/v1/info/report/config", headers=_auth(key))
    assert response.status_code == 200
    assert response.json()["enabled"] is True


def test_report_disabled_returns_503(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    from app.config import reset_settings

    monkeypatch.setenv("INFO_REPORT_ENABLED", "false")
    reset_settings()
    key = _create_key(client, auth_headers)
    response = client.post("/v1/info/report", headers=_auth(key), json={"text": "x"})
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "service_disabled"


def test_report_rate_limited(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    from app.config import reset_settings
    from app.services.info_report_gate import reset_info_report_gate

    monkeypatch.setenv("INFO_REPORT_RATE_PER_MINUTE", "1")
    reset_settings()
    reset_info_report_gate()
    key = _create_key(client, auth_headers)
    first = client.post("/v1/info/report", headers=_auth(key), json={"text": "r1"})
    assert first.status_code == 200, first.text
    second = client.post("/v1/info/report", headers=_auth(key), json={"text": "r2"})
    assert second.status_code == 429
    assert second.json()["error"]["type"] == "rate_limited"


def test_call_logs_recorded(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _create_key(client, auth_headers)
    client.post("/v1/info/report", headers=_auth(key), json={"text": "logged"})
    logs = client.get("/api/admin/mcp/calls?capability_id=info", headers=auth_headers).json()
    assert logs["total"] >= 1
    assert any(item["operation"] == "report" and item["success"] for item in logs["items"])


def test_provider_mcp_base64_dispatch(client: TestClient, auth_headers: dict[str, str]) -> None:
    import asyncio

    from app.capabilities.base import CallContext
    from app.capabilities.info.provider import InfoReportProvider
    from app.db import get_session_factory
    from app.services.mcp_auth import resolve_mcp_key

    key = _create_key(client, auth_headers)
    session = get_session_factory()()
    try:
        mcp_key = resolve_mcp_key(session, key)
        assert mcp_key is not None
        provider = InfoReportProvider()
        result = asyncio.run(
            provider.dispatch(
                "report",
                {
                    "text": "mcp base64",
                    "media": [{"filename": "a.png", "content_base64": base64.b64encode(_png()).decode()}],
                },
                CallContext(db=session, mcp_key=mcp_key),
            )
        )
        session.commit()
        assert result["media_count"] == 1
        assert result["duplicate"] is False
    finally:
        session.close()


def test_mcp_tools_list_includes_info(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _create_key(client, auth_headers)
    response = client.post(
        "/mcp",
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        follow_redirects=False,
    )
    assert response.status_code == 200, response.text
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert "info_report" in names

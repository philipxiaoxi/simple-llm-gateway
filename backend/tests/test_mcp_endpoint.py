from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}

JSON_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _mcp_key(client: TestClient, auth_headers: dict[str, str]) -> str:
    created = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "agent", "capability_ids": ["knowledge"]},
    )
    assert created.status_code == 201, created.text
    return created.json()["key"]


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_mcp_endpoint_requires_authentication(client: TestClient, path: str) -> None:
    response = client.post(path, json=INITIALIZE, headers={"Content-Type": "application/json"}, follow_redirects=False)
    assert response.status_code == 401
    assert "text/html" not in response.headers.get("content-type", "")
    assert response.json()["error"]["type"] == "authentication_error"


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_mcp_initialize_reaches_handler(
    client: TestClient, auth_headers: dict[str, str], path: str
) -> None:
    """合法 Key 必须真正进到 FastMCP handler，而不是只过鉴权就 500。"""
    key = _mcp_key(client, auth_headers)
    response = client.post(
        path,
        json=INITIALIZE,
        headers={**JSON_HEADERS, "Authorization": f"Bearer {key}"},
        follow_redirects=False,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["result"]["serverInfo"]["name"]


def test_mcp_get_without_key_is_not_html(client: TestClient) -> None:
    response = client.get("/mcp", headers={"Accept": "text/event-stream"}, follow_redirects=False)
    assert response.status_code == 401
    assert "text/html" not in response.headers.get("content-type", "")


def test_mcp_query_key_is_not_authenticated(client: TestClient, auth_headers: dict[str, str]) -> None:
    """鉴权只认 Header；query 里的密钥不参与认证。"""
    key = _mcp_key(client, auth_headers)
    response = client.post(
        f"/mcp?key={key}",
        json=INITIALIZE,
        headers=JSON_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 401


def test_redact_secret_query() -> None:
    from app.main import redact_secret_query

    assert redact_secret_query(b"key=mcp-secret&foo=1") == b"key=***&foo=1"
    assert redact_secret_query(b"api_key=abc&x=y") == b"api_key=***&x=y"
    assert redact_secret_query(b"foo=1") == b"foo=1"
    assert redact_secret_query(b"") == b""


def test_mcp_plaza_is_still_frontend_route(client: TestClient) -> None:
    from app.main import FRONTEND_DIST

    if not FRONTEND_DIST.exists():
        pytest.skip("frontend dist 不存在")
    response = client.get("/mcp-plaza")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")

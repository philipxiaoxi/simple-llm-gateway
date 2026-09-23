from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_mcp_root_is_not_spa_html(client: TestClient) -> None:
    response = client.get("/mcp")
    assert response.status_code == 401
    assert "text/html" not in response.headers.get("content-type", "")
    assert response.json()["error"]["type"] == "authentication_error"


def test_mcp_root_post_is_not_404(client: TestClient) -> None:
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"


def test_mcp_slash_and_root_both_hit_auth(client: TestClient) -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    for path in ("/mcp", "/mcp/"):
        response = client.post(path, json=payload)
        assert response.status_code == 401, path
        assert "text/html" not in response.headers.get("content-type", "")
        assert response.json()["error"]["type"] == "authentication_error"


def test_mcp_plaza_is_still_frontend_route(client: TestClient) -> None:
    from app.main import FRONTEND_DIST

    if not FRONTEND_DIST.exists():
        pytest.skip("frontend dist 不存在")
    response = client.get("/mcp-plaza")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")

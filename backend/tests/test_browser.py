from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.capabilities.browser.errors import BrowserError

FAKE_PNG = b"FAKEPNG"


class FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"

    async def goto(self, url: str, **_kwargs: Any) -> dict[str, Any]:
        self.url = url
        return {"url": url, "title": "Fake"}

    async def snapshot(self, *, max_chars: int = 6000) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": "Fake",
            "snapshot": '[ref=e1] button "Go"',
            "refs": [{"ref": "e1", "role": "button", "label": "Go"}],
        }

    async def screenshot(self, **_kwargs: Any) -> tuple[bytes, str, int, int]:
        return FAKE_PNG, "image/png", 4, 3

    async def click(self, **_kwargs: Any) -> None:
        return None

    async def type_text(self, **_kwargs: Any) -> None:
        return None

    async def press(self, **_kwargs: Any) -> None:
        return None

    async def select(self, **_kwargs: Any) -> None:
        return None

    async def scroll(self, **_kwargs: Any) -> None:
        return None

    async def wait(self, **_kwargs: Any) -> None:
        return None

    async def back(self) -> dict[str, Any]:
        return await self.state()

    async def forward(self) -> dict[str, Any]:
        return await self.state()

    async def state(self) -> dict[str, Any]:
        return {"url": self.url, "title": "Fake"}

    async def close(self) -> None:
        return None


class FakeBackend:
    name = "fake"

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def new_session(self, **_kwargs: Any) -> FakePage:
        return FakePage()


def _setup(monkeypatch) -> Any:
    from app.capabilities import registry
    from app.capabilities.browser import manager
    from app.capabilities.browser.provider import BrowserProvider

    registry.ensure_defaults()
    if registry.get_provider("browser") is None:
        registry.register(BrowserProvider())
    manager.configure_engine(FakeBackend())
    monkeypatch.setattr(
        "app.capabilities.browser.provider.assert_navigable_url",
        lambda url, **_kwargs: "example.com",
    )
    return manager


def _key(client: TestClient, auth_headers: dict[str, str]) -> str:
    response = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "browser-key", "capability_ids": ["browser"]},
    )
    assert response.status_code == 201, response.text
    return response.json()["key"]


def test_browser_session_flow(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    manager = _setup(monkeypatch)
    try:
        key = _key(client, auth_headers)
        auth = {"Authorization": f"Bearer {key}"}

        created = client.post("/v1/browser/sessions", headers=auth, json={"url": "https://example.com"})
        assert created.status_code == 201, created.text
        body = created.json()
        session_id = body["session_id"]
        assert body["title"] == "Fake"
        assert body["refs"][0]["ref"] == "e1"

        nav = client.post(
            f"/v1/browser/sessions/{session_id}/actions/navigate",
            headers=auth,
            json={"url": "https://example.org"},
        )
        assert nav.status_code == 200, nav.text
        assert nav.json()["url"] == "https://example.org"

        shot = client.post(
            f"/v1/browser/sessions/{session_id}/actions/screenshot", headers=auth, json={}
        )
        assert shot.status_code == 200, shot.text
        image = client.get(shot.json()["screenshot_url"])
        assert image.status_code == 200
        assert image.content == FAKE_PNG

        listed = client.get("/v1/browser/sessions", headers=auth)
        assert listed.status_code == 200
        assert any(item["id"] == session_id for item in listed.json()["items"])

        closed = client.delete(f"/v1/browser/sessions/{session_id}", headers=auth)
        assert closed.status_code == 200, closed.text
        detail = client.get(f"/v1/browser/sessions/{session_id}", headers=auth)
        assert detail.json()["status"] == "closed"
    finally:
        manager.reset_for_tests()


def test_browser_session_limit(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    manager = _setup(monkeypatch)
    try:
        key = _key(client, auth_headers)
        auth = {"Authorization": f"Bearer {key}"}
        for index in range(2):
            response = client.post(
                "/v1/browser/sessions", headers=auth, json={"url": f"https://example.com/{index}"}
            )
            assert response.status_code == 201, response.text
        third = client.post("/v1/browser/sessions", headers=auth, json={"url": "https://example.com/3"})
        assert third.status_code == 429, third.text
        assert third.json()["error"]["type"] == "too_many_sessions"
    finally:
        manager.reset_for_tests()


def test_browser_requires_capability(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    manager = _setup(monkeypatch)
    try:
        response = client.post(
            "/api/admin/mcp/keys",
            headers=auth_headers,
            json={"name": "no-browser", "capability_ids": ["knowledge"]},
        )
        key = response.json()["key"]
        denied = client.post(
            "/v1/browser/sessions",
            headers={"Authorization": f"Bearer {key}"},
            json={"url": "https://example.com"},
        )
        assert denied.status_code == 403
    finally:
        manager.reset_for_tests()


def test_browser_admin_endpoints(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    manager = _setup(monkeypatch)
    try:
        key = _key(client, auth_headers)
        auth = {"Authorization": f"Bearer {key}"}
        created = client.post("/v1/browser/sessions", headers=auth, json={"url": "https://example.com"})
        session_id = created.json()["session_id"]

        status = client.get("/api/admin/mcp/browser/status", headers=auth_headers)
        assert status.status_code == 200, status.text
        assert "active_sessions" in status.json()

        sessions = client.get("/api/admin/mcp/browser/sessions", headers=auth_headers)
        assert sessions.status_code == 200, sessions.text
        assert any(item["id"] == session_id for item in sessions.json()["items"])

        closed = client.post(
            f"/api/admin/mcp/browser/sessions/{session_id}/close", headers=auth_headers
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "closed"
    finally:
        manager.reset_for_tests()


def test_browser_url_policy() -> None:
    from app.capabilities.browser.policy import assert_navigable_url

    with pytest.raises(BrowserError):
        assert_navigable_url("file:///etc/passwd")
    with pytest.raises(BrowserError):
        assert_navigable_url("http://127.0.0.1/")
    with pytest.raises(BrowserError):
        assert_navigable_url("https://example.com", blocked_hosts="example.com")


def test_screenshot_token_roundtrip() -> None:
    from app.capabilities.browser import tokens

    token = tokens.make_token("shot-1", ttl_seconds=60)
    assert tokens.screenshot_id_from_token(token) == "shot-1"
    assert tokens.screenshot_id_from_token(token + "x") is None
    assert tokens.screenshot_id_from_token(None) is None

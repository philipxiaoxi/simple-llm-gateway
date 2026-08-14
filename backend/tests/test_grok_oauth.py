from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def test_oauth_start_returns_url(client: TestClient, auth_headers: dict[str, str]) -> None:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok"},
    ).json()
    response = client.get(f"/api/admin/accounts/{account['id']}/oauth/start", headers=auth_headers)
    assert response.status_code == 200
    url = response.json()["authorize_url"]
    assert url.startswith("https://auth.x.ai/oauth2/authorize")
    assert "client_id=b1a00492-073a-47ea-816f-4c329264a828" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A56121%2Fcallback" in url
    assert "code_challenge=" in url
    assert "state=" in url


def test_oauth_callback_stores_token(client: TestClient, auth_headers: dict[str, str]) -> None:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok"},
    ).json()
    start = client.get(f"/api/admin/accounts/{account['id']}/oauth/start", headers=auth_headers)
    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(start.json()["authorize_url"]).query)["state"][0]

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {
                "access_token": "access-xyz",
                "refresh_token": "refresh-xyz",
                "expires_in": 3600,
                "scope": "api:access",
            }

    with patch("app.services.grok_oauth.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.post = AsyncMock(return_value=FakeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        response = client.get(
            "/api/admin/oauth/grok/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )
    assert response.status_code in {302, 307}
    detail = client.get(f"/api/admin/accounts/{account['id']}", headers=auth_headers)
    assert detail.json()["has_credential"] is True

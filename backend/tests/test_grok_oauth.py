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
    assert response.json()["needs_paste"] is True


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


def test_oauth_complete_from_pasted_loopback_url(client: TestClient, auth_headers: dict[str, str]) -> None:
    from urllib.parse import parse_qs, urlparse

    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok"},
    ).json()
    start = client.get(f"/api/admin/accounts/{account['id']}/oauth/start", headers=auth_headers)
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
        response = client.post(
            "/api/admin/oauth/grok/callback",
            headers=auth_headers,
            json={
                "account_id": account["id"],
                "callback_url": f"http://127.0.0.1:56121/callback?code=abc&state={state}",
            },
        )
    assert response.status_code == 200, response.text
    detail = client.get(f"/api/admin/accounts/{account['id']}", headers=auth_headers)
    assert detail.json()["has_credential"] is True


def test_parse_oauth_callback_requires_code_and_state() -> None:
    from app.services.grok_oauth import parse_oauth_callback

    code, state = parse_oauth_callback("http://127.0.0.1:56121/callback?code=abc&state=xyz")
    assert code == "abc"
    assert state == "xyz"
    try:
        parse_oauth_callback("http://127.0.0.1:56121/callback")
    except ValueError as error:
        assert "完整" in str(error)
    else:
        raise AssertionError("expected ValueError")


def test_oauth_complete_rejects_consent_page_url(client: TestClient, auth_headers: dict[str, str]) -> None:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok"},
    ).json()
    client.get(f"/api/admin/accounts/{account['id']}/oauth/start", headers=auth_headers)
    response = client.post(
        "/api/admin/oauth/grok/callback",
        headers=auth_headers,
        json={
            "account_id": account["id"],
            "callback_url": (
                "https://accounts.x.ai/oauth2/consent?response_type=code"
                "&client_id=b1a00492-073a-47ea-816f-4c329264a828"
                "&redirect_uri=http%3A%2F%2F127.0.0.1%3A56121%2Fcallback"
                "&state=abc&code_challenge=xyz&code_challenge_method=S256"
            ),
        },
    )
    assert response.status_code == 400
    assert "授权页" in response.json()["detail"]


def test_oauth_complete_stores_pasted_api_key(client: TestClient, auth_headers: dict[str, str]) -> None:
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import OAuthToken

    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok"},
    ).json()
    _complete_grok_oauth(client, auth_headers, account["id"])
    response = client.post(
        "/api/admin/oauth/grok/callback",
        headers=auth_headers,
        json={"account_id": account["id"], "callback_url": "xai-pasted-from-consent"},
    )
    assert response.status_code == 200, response.text
    detail = client.get(f"/api/admin/accounts/{account['id']}?reveal=1", headers=auth_headers)
    assert detail.json()["has_credential"] is True
    assert detail.json()["api_key"] == "xai-pasted-from-consent"
    session = get_session_factory()()
    try:
        leftover = session.scalar(select(OAuthToken).where(OAuthToken.account_id == account["id"]))
        assert leftover is None
    finally:
        session.close()


def test_oauth_complete_rejects_callback_for_other_account(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    first = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok A", "provider": "grok"},
    ).json()
    second = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok B", "provider": "grok"},
    ).json()
    start = client.get(f"/api/admin/accounts/{first['id']}/oauth/start", headers=auth_headers)
    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(start.json()["authorize_url"]).query)["state"][0]
    response = client.post(
        "/api/admin/oauth/grok/callback",
        headers=auth_headers,
        json={
            "account_id": second["id"],
            "callback_url": f"http://127.0.0.1:56121/callback?code=abc&state={state}",
        },
    )
    assert response.status_code == 400
    assert "当前账号" in response.json()["detail"]


def test_oauth_complete_from_bare_code(client: TestClient, auth_headers: dict[str, str]) -> None:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok"},
    ).json()
    client.get(f"/api/admin/accounts/{account['id']}/oauth/start", headers=auth_headers)

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"access_token": "access-xyz", "refresh_token": "refresh-xyz", "expires_in": 3600}

    with patch("app.services.grok_oauth.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.post = AsyncMock(return_value=FakeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        response = client.post(
            "/api/admin/oauth/grok/callback",
            headers=auth_headers,
            json={"account_id": account["id"], "callback_url": "bare-auth-code"},
        )
    assert response.status_code == 200, response.text
    detail = client.get(f"/api/admin/accounts/{account['id']}", headers=auth_headers)
    assert detail.json()["has_credential"] is True


def test_delete_account_after_oauth_start(client: TestClient, auth_headers: dict[str, str]) -> None:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok"},
    ).json()
    first = client.get(f"/api/admin/accounts/{account['id']}/oauth/start", headers=auth_headers)
    second = client.get(f"/api/admin/accounts/{account['id']}/oauth/start", headers=auth_headers)
    assert first.status_code == 200
    assert second.status_code == 200
    deleted = client.delete(f"/api/admin/accounts/{account['id']}", headers=auth_headers)
    assert deleted.status_code == 200, deleted.text
    listed = client.get("/api/admin/accounts", headers=auth_headers)
    assert listed.json() == []


def test_refresh_if_needed_exchanges_refresh_token(client: TestClient, auth_headers: dict[str, str]) -> None:
    import asyncio
    from datetime import datetime, timedelta

    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import OAuthToken, UpstreamAccount
    from app.services.grok_oauth import refresh_if_needed

    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok"},
    ).json()
    client.get(f"/api/admin/accounts/{account['id']}/oauth/start", headers=auth_headers)

    class ExchangeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"access_token": "old-access", "refresh_token": "refresh-xyz", "expires_in": 3600}

    class RefreshResponse:
        status_code = 200

        def json(self) -> dict:
            return {"access_token": "new-access", "refresh_token": "refresh-xyz", "expires_in": 3600}

    with patch("app.services.grok_oauth.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.post = AsyncMock(return_value=ExchangeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        client.post(
            "/api/admin/oauth/grok/callback",
            headers=auth_headers,
            json={"account_id": account["id"], "callback_url": "bare-auth-code"},
        )

    session = get_session_factory()()
    try:
        stored = session.scalar(select(OAuthToken).where(OAuthToken.account_id == account["id"]))
        assert stored is not None
        stored.expires_at = datetime.utcnow() - timedelta(minutes=5)
        session.commit()
        session.expire_all()
        stored_account = session.get(UpstreamAccount, account["id"])
        assert stored_account is not None
        assert stored_account.oauth_token is not None
        assert stored_account.oauth_token.expires_at < datetime.utcnow()
        with patch("app.services.grok_oauth.httpx.AsyncClient") as client_cls:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=RefreshResponse())
            instance.__aenter__.return_value = instance
            instance.__aexit__.return_value = None
            client_cls.return_value = instance
            access = asyncio.run(refresh_if_needed(session, stored_account))
            session.commit()
        assert access == "new-access"
        assert instance.post.await_args.kwargs["data"]["grant_type"] == "refresh_token"
        assert instance.post.await_args.kwargs["data"]["refresh_token"] == "refresh-xyz"
    finally:
        session.close()


def _complete_grok_oauth(client: TestClient, auth_headers: dict[str, str], account_id: int) -> None:
    client.get(f"/api/admin/accounts/{account_id}/oauth/start", headers=auth_headers)

    class ExchangeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"access_token": "old-access", "refresh_token": "refresh-xyz", "expires_in": 3600}

    with patch("app.services.grok_oauth.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.post = AsyncMock(return_value=ExchangeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        completed = client.post(
            "/api/admin/oauth/grok/callback",
            headers=auth_headers,
            json={"account_id": account_id, "callback_url": "bare-auth-code"},
        )
    assert completed.status_code == 200, completed.text


def test_accounts_due_for_oauth_refresh_only_soon_expiring(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    from datetime import datetime, timedelta

    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import OAuthToken
    from app.services.grok_oauth import accounts_due_for_oauth_refresh

    soon = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Soon", "provider": "grok"},
    ).json()
    later = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Later", "provider": "grok"},
    ).json()
    _complete_grok_oauth(client, auth_headers, soon["id"])
    _complete_grok_oauth(client, auth_headers, later["id"])

    session = get_session_factory()()
    try:
        for token in session.scalars(select(OAuthToken)).all():
            if token.account_id == soon["id"]:
                token.expires_at = datetime.utcnow() + timedelta(minutes=5)
            else:
                token.expires_at = datetime.utcnow() + timedelta(hours=2)
        session.commit()
        due_ids = {account.id for account in accounts_due_for_oauth_refresh(session)}
        assert due_ids == {soon["id"]}
    finally:
        session.close()


def test_refresh_expiring_oauth_tokens_updates_due_account(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    import asyncio
    from datetime import datetime, timedelta

    from sqlalchemy import select

    from app.crypto import decrypt_secret
    from app.db import get_session_factory
    from app.config import get_settings
    from app.models import OAuthToken
    from app.services.grok_oauth import refresh_expiring_oauth_tokens

    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok"},
    ).json()
    _complete_grok_oauth(client, auth_headers, account["id"])

    session = get_session_factory()()
    try:
        token = session.scalar(select(OAuthToken).where(OAuthToken.account_id == account["id"]))
        assert token is not None
        token.expires_at = datetime.utcnow() + timedelta(minutes=5)
        session.commit()
    finally:
        session.close()

    class RefreshResponse:
        status_code = 200

        def json(self) -> dict:
            return {"access_token": "rotated-access", "refresh_token": "refresh-xyz", "expires_in": 3600}

    with patch("app.services.grok_oauth.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.post = AsyncMock(return_value=RefreshResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        refreshed = asyncio.run(refresh_expiring_oauth_tokens())
    assert refreshed == 1

    session = get_session_factory()()
    try:
        token = session.scalar(select(OAuthToken).where(OAuthToken.account_id == account["id"]))
        assert token is not None
        assert decrypt_secret(token.access_token_encrypted, get_settings().app_secret_key) == "rotated-access"
    finally:
        session.close()

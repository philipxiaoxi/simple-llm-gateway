from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def test_list_providers(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/admin/providers", headers=auth_headers)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()}
    assert ids == {"opencode_go", "grok", "deepseek"}


def test_create_account_encrypts_key(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-upstream-secret"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["provider"] == "deepseek"
    assert body["has_credential"] is True
    assert body.get("api_key") is None

    listed = client.get("/api/admin/accounts", headers=auth_headers)
    assert listed.json()[0]["api_key"] is None

    revealed = client.get(f"/api/admin/accounts/{body['id']}?reveal=1", headers=auth_headers)
    assert revealed.json()["api_key"] == "sk-upstream-secret"


def test_probe_updates_account(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    )
    account_id = created.json()["id"]

    class FakeResponse:
        status_code = 200
        text = '{"data":[]}'

    with patch("app.services.probe.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=FakeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        response = client.post(f"/api/admin/accounts/{account_id}/probe", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["ok"] is True
    detail = client.get(f"/api/admin/accounts/{account_id}", headers=auth_headers)
    assert detail.json()["last_probe_ok"] is True


def test_quota_deepseek(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    )
    account_id = created.json()["id"]

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "9.9"}]}

    with patch("app.services.quota.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=FakeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        response = client.post(f"/api/admin/accounts/{account_id}/quota", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["supported"] is True
    assert response.json()["raw"]["is_available"] is True


def test_quota_opencode_go_windows(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "OC", "provider": "opencode_go", "api_key": "sk-oc"},
    )
    account_id = created.json()["id"]

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {
                "usage": {
                    "rolling": {"status": "ok", "percent": 0, "resetsAt": "2026-08-14T19:33:32.193Z"},
                    "weekly": {"status": "ok", "percent": 4, "resetsAt": "2026-08-17T00:00:00.193Z"},
                    "monthly": {"status": "ok", "percent": 3, "resetsAt": "2026-09-01T13:50:58.193Z"},
                }
            }

    with patch("app.services.quota.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=FakeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        response = client.post(f"/api/admin/accounts/{account_id}/quota", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["supported"] is True
    assert body["ok"] is True
    windows = {item["id"]: item for item in body["windows"]}
    assert windows["rolling"]["label"] == "5 小时限额"
    assert windows["rolling"]["percent"] == 0
    assert windows["rolling"]["limit_usd"] == 12
    assert windows["weekly"]["label"] == "周限制"
    assert windows["weekly"]["percent"] == 4
    assert windows["weekly"]["used_usd"] == 1.2
    assert windows["monthly"]["label"] == "月限制"
    assert windows["monthly"]["percent"] == 3
    stored = client.get(f"/api/admin/accounts/{account_id}", headers=auth_headers)
    assert stored.json()["quota"]["windows"][0]["id"] == "rolling"


def test_parse_grok_weekly_window() -> None:
    from app.services.quota import parse_grok_weekly_windows

    windows = parse_grok_weekly_windows(
        {
            "config": {
                "currentPeriod": {
                    "type": "USAGE_PERIOD_TYPE_WEEKLY",
                    "start": "2026-08-09T09:32:10.577883+00:00",
                    "end": "2026-08-16T09:32:10.577883+00:00",
                },
                "creditUsagePercent": 25.0,
                "productUsage": [{"product": "GrokBuild", "usagePercent": 25.0}],
            }
        }
    )
    assert len(windows) == 1
    assert windows[0]["id"] == "weekly"
    assert windows[0]["label"] == "周限制"
    assert windows[0]["percent"] == 25.0
    assert windows[0]["resets_at"] == "2026-08-16T09:32:10.577883+00:00"
    assert windows[0].get("limit_usd") is None


def test_quota_grok_weekly(client: TestClient, auth_headers: dict[str, str]) -> None:
    from urllib.parse import parse_qs, urlparse

    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok"},
    ).json()
    start = client.get(f"/api/admin/accounts/{created['id']}/oauth/start", headers=auth_headers)
    state = parse_qs(urlparse(start.json()["authorize_url"]).query)["state"][0]

    class TokenResponse:
        status_code = 200

        def json(self) -> dict:
            return {"access_token": "access-xyz", "refresh_token": "refresh-xyz", "expires_in": 3600}

    class BillingResponse:
        status_code = 200
        text = "{}"

        def json(self) -> dict:
            return {
                "config": {
                    "currentPeriod": {
                        "type": "USAGE_PERIOD_TYPE_WEEKLY",
                        "end": "2026-08-16T09:32:10.577883+00:00",
                    },
                    "creditUsagePercent": 25.0,
                }
            }

    with patch("app.services.grok_oauth.httpx.AsyncClient") as token_client:
        token_instance = AsyncMock()
        token_instance.post = AsyncMock(return_value=TokenResponse())
        token_instance.__aenter__.return_value = token_instance
        token_instance.__aexit__.return_value = None
        token_client.return_value = token_instance
        callback = client.get(
            "/api/admin/oauth/grok/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )
    assert callback.status_code in {302, 307}

    with patch("app.services.quota.httpx.AsyncClient") as billing_client:
        billing_instance = AsyncMock()
        billing_instance.get = AsyncMock(return_value=BillingResponse())
        billing_instance.__aenter__.return_value = billing_instance
        billing_instance.__aexit__.return_value = None
        billing_client.return_value = billing_instance
        response = client.post(f"/api/admin/accounts/{created['id']}/quota", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["windows"][0]["id"] == "weekly"
    assert body["windows"][0]["percent"] == 25.0
    assert billing_instance.get.await_args.args[0].endswith("/billing?format=credits")


def test_list_account_models(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    )
    account_id = created.json()["id"]

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}

    with patch("app.services.probe.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=FakeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        response = client.post(f"/api/admin/accounts/{account_id}/models", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["models"] == ["deepseek-chat", "deepseek-reasoner"]
    stored = client.get(f"/api/admin/accounts/{account_id}", headers=auth_headers)
    assert stored.json()["models"] == ["deepseek-chat", "deepseek-reasoner"]

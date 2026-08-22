from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def test_list_providers(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/admin/providers", headers=auth_headers)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()}
    assert ids == {"opencode_go", "grok", "deepseek", "openai_generic", "anthropic_generic"}
    by_id = {item["id"]: item for item in response.json()}
    assert by_id["openai_generic"]["label"] == "通用 OpenAI"
    assert by_id["openai_generic"]["base_url"] == "https://api.openai.com/v1"
    assert by_id["anthropic_generic"]["label"] == "通用 Anthropic"
    assert by_id["anthropic_generic"]["base_url"] == "https://api.anthropic.com/v1"


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


def test_create_account_custom_base_url(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up", "base_url": " https://proxy.example/ds "},
    )
    assert created.status_code == 200
    assert created.json()["base_url"] == "https://proxy.example/ds"


def test_update_account_base_url(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    )
    account_id = created.json()["id"]
    updated = client.patch(
        f"/api/admin/accounts/{account_id}",
        headers=auth_headers,
        json={"base_url": " https://proxy.example/v1 "},
    )
    assert updated.status_code == 200
    assert updated.json()["base_url"] == "https://proxy.example/v1"


def test_create_and_update_account_website_url(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={
            "name": "DS",
            "provider": "deepseek",
            "api_key": "sk-up",
            "website_url": " https://platform.deepseek.com/ ",
        },
    )
    assert created.status_code == 200
    account_id = created.json()["id"]
    assert created.json()["website_url"] == "https://platform.deepseek.com/"

    updated = client.patch(
        f"/api/admin/accounts/{account_id}",
        headers=auth_headers,
        json={"website_url": " https://console.example/ "},
    )
    assert updated.status_code == 200
    assert updated.json()["website_url"] == "https://console.example/"

    cleared = client.patch(
        f"/api/admin/accounts/{account_id}",
        headers=auth_headers,
        json={"website_url": ""},
    )
    assert cleared.status_code == 200
    assert cleared.json()["website_url"] is None


def test_account_website_url_requires_http_or_https(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "website_url": "javascript:alert(1)"},
    )
    assert response.status_code == 422


def test_delete_account(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    )
    account_id = created.json()["id"]
    deleted = client.delete(f"/api/admin/accounts/{account_id}", headers=auth_headers)
    assert deleted.status_code == 200
    listed = client.get("/api/admin/accounts", headers=auth_headers)
    assert listed.json() == []


def test_delete_account_blocked_when_keys_exist(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    )
    account_id = created.json()["id"]
    client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account_id},
    )
    deleted = client.delete(f"/api/admin/accounts/{account_id}", headers=auth_headers)
    assert deleted.status_code == 400
    assert "API Key" in deleted.json()["detail"]


def test_delete_account_keeps_request_logs(client: TestClient, auth_headers: dict[str, str]) -> None:
    from unittest.mock import AsyncMock, patch

    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    )
    account_id = created.json()["id"]
    key = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account_id},
    ).json()
    key_id = key["id"]

    class FakeResponse:
        def model_dump(self) -> dict:
            return {
                "id": "chatcmpl-test",
                "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
        proxied = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key['key']}"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert proxied.status_code == 200
    assert client.delete(f"/api/admin/keys/{key_id}", headers=auth_headers).status_code == 200
    deleted = client.delete(f"/api/admin/accounts/{account_id}", headers=auth_headers)
    assert deleted.status_code == 200
    leftover = client.get("/api/admin/logs", headers=auth_headers).json()["items"][0]
    assert leftover["account_id"] == account_id
    assert leftover["account_name"] == "DS"
    assert leftover["api_key_id"] == key_id
    assert leftover["api_key_name"] == "k"


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

    with patch("app.providers.base.httpx.AsyncClient") as client_cls:
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

    with patch("app.providers.deepseek.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=FakeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        response = client.post(f"/api/admin/accounts/{account_id}/quota", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["items"][0] == {"label": "USD", "type": "text", "value": "$9.90"}
    assert body["items"][1]["label"] == "构成"
    assert "赠送 $0.00" in body["items"][1]["value"]
    assert "充值 $0.00" in body["items"][1]["value"]
    stored = client.get(f"/api/admin/accounts/{account_id}", headers=auth_headers)
    assert stored.json()["quota"]["items"][0]["value"] == "$9.90"


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

    with patch("app.providers.opencode_go.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=FakeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        response = client.post(f"/api/admin/accounts/{account_id}/quota", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    items = body["items"]
    progress = {item["label"]: item["value"] for item in items if item["type"] == "progress"}
    texts = {item["label"]: item["value"] for item in items if item["type"] == "text"}
    assert progress["5 小时限额"] == 0
    assert progress["周限制"] == 4
    assert progress["月限制"] == 3
    assert "$0.00 / $12.00" in texts["5 小时限额"]
    assert "$1.20 / $30.00" in texts["周限制"]
    stored = client.get(f"/api/admin/accounts/{account_id}", headers=auth_headers)
    assert stored.json()["quota"]["items"][0]["label"] == "5 小时限额"


def test_parse_grok_weekly_window() -> None:
    from app.providers.grok import grok_quota_items

    items = grok_quota_items(
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
    assert items[0].label == "周限制"
    assert items[0].type == "progress"
    assert items[0].value == 25.0
    assert items[1].type == "text"
    assert items[1].value == "重置时间：2026-08-16T09:32:10.577883+00:00"


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

    with patch("app.providers.grok.httpx.AsyncClient") as billing_client:
        billing_instance = AsyncMock()
        billing_instance.get = AsyncMock(return_value=BillingResponse())
        billing_instance.__aenter__.return_value = billing_instance
        billing_instance.__aexit__.return_value = None
        billing_client.return_value = billing_instance
        response = client.post(f"/api/admin/accounts/{created['id']}/quota", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["items"][0] == {"label": "周限制", "type": "progress", "value": 25.0}
    assert body["items"][1]["value"] == "重置时间：2026-08-16T09:32:10.577883+00:00"
    assert billing_instance.get.await_args.args[0].endswith("/billing?format=credits")


def test_quota_generic_providers_skip_network(client: TestClient, auth_headers: dict[str, str]) -> None:
    for provider_id in ("openai_generic", "anthropic_generic"):
        created = client.post(
            "/api/admin/accounts",
            headers=auth_headers,
            json={"name": provider_id, "provider": provider_id, "api_key": "sk-up"},
        )
        assert created.status_code == 200
        assert created.json()["base_url"] in {"https://api.openai.com/v1", "https://api.anthropic.com/v1"}
        assert created.json()["quota"]["ok"] is False
        assert created.json()["quota"]["message"] == "通用供应商不支持查询余额"

        with patch("app.providers.base.httpx.AsyncClient") as client_cls:
            response = client.post(f"/api/admin/accounts/{created.json()['id']}/quota", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert response.json()["message"] == "通用供应商不支持查询余额"
        client_cls.assert_not_called()


def test_anthropic_generic_probe_uses_x_api_key(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Claude", "provider": "anthropic_generic", "api_key": "sk-ant"},
    )
    account_id = created.json()["id"]

    class FakeResponse:
        status_code = 200
        text = '{"data":[]}'

    with patch("app.providers.base.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=FakeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        response = client.post(f"/api/admin/accounts/{account_id}/probe", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["ok"] is True
    headers = instance.get.await_args.kwargs["headers"]
    assert headers["x-api-key"] == "sk-ant"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in headers
    assert instance.get.await_args.args[0] == "https://api.anthropic.com/v1/models"


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

    with patch("app.providers.base.httpx.AsyncClient") as client_cls:
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


def test_export_import_accounts_roundtrip(client: TestClient, auth_headers: dict[str, str]) -> None:
    client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-upstream-secret"},
    )
    client.post("/api/admin/accounts", headers=auth_headers, json={"name": "Grok", "provider": "grok"})
    exported = client.post(
        "/api/admin/accounts/export",
        headers=auth_headers,
        json={"password": "long-pass-1"},
    )
    assert exported.status_code == 200
    envelope = exported.json()
    assert envelope["kdf"] == "pbkdf2_sha256"
    assert "ciphertext" in envelope

    imported = client.post(
        "/api/admin/accounts/import",
        headers=auth_headers,
        json={"password": "long-pass-1", "payload": envelope},
    )
    assert imported.status_code == 200
    assert imported.json()["created"] == 2
    names = {item["name"] for item in client.get("/api/admin/accounts", headers=auth_headers).json()}
    assert "DS" in names
    assert "DS（1）" in names
    assert "Grok" in names
    assert "Grok（1）" in names

    copy = next(item for item in client.get("/api/admin/accounts", headers=auth_headers).json() if item["name"] == "DS（1）")
    revealed = client.get(f"/api/admin/accounts/{copy['id']}?reveal=1", headers=auth_headers).json()
    assert revealed["api_key"] == "sk-upstream-secret"
    grok_copy = next(item for item in client.get("/api/admin/accounts", headers=auth_headers).json() if item["name"] == "Grok（1）")
    assert grok_copy["has_credential"] is False


def test_export_import_skips_grok_oauth_tokens(client: TestClient, auth_headers: dict[str, str]) -> None:
    import json

    from sqlalchemy import select

    from app.crypto import decrypt_with_password
    from app.db import get_session_factory
    from app.models import OAuthToken

    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok"},
    ).json()
    client.get(f"/api/admin/accounts/{created['id']}/oauth/start", headers=auth_headers)

    class TokenResponse:
        status_code = 200

        def json(self) -> dict:
            return {"access_token": "access-xyz", "refresh_token": "refresh-xyz", "expires_in": 3600}

    with patch("app.services.grok_oauth.httpx.AsyncClient") as token_client:
        instance = AsyncMock()
        instance.post = AsyncMock(return_value=TokenResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        token_client.return_value = instance
        completed = client.post(
            "/api/admin/oauth/grok/callback",
            headers=auth_headers,
            json={"account_id": created["id"], "callback_url": "bare-auth-code"},
        )
    assert completed.status_code == 200, completed.text
    assert client.get(f"/api/admin/accounts/{created['id']}", headers=auth_headers).json()["has_credential"] is True

    envelope = client.post(
        "/api/admin/accounts/export",
        headers=auth_headers,
        json={"password": "long-pass-1"},
    ).json()
    payload = json.loads(decrypt_with_password(envelope, "long-pass-1"))
    grok_entry = next(item for item in payload["accounts"] if item["name"] == "Grok")
    assert grok_entry["api_key"] is None
    assert "oauth_token" not in grok_entry
    assert "access_token" not in grok_entry
    assert "refresh_token" not in grok_entry

    imported = client.post(
        "/api/admin/accounts/import",
        headers=auth_headers,
        json={"password": "long-pass-1", "payload": envelope},
    )
    assert imported.status_code == 200
    copy = next(item for item in client.get("/api/admin/accounts", headers=auth_headers).json() if item["name"] == "Grok（1）")
    assert copy["has_credential"] is False

    session = get_session_factory()()
    try:
        token_account_ids = set(session.scalars(select(OAuthToken.account_id)).all())
        assert created["id"] in token_account_ids
        assert copy["id"] not in token_account_ids
    finally:
        session.close()


def test_export_password_too_short(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post("/api/admin/accounts/export", headers=auth_headers, json={"password": "short"})
    assert response.status_code == 400


def test_import_wrong_password(client: TestClient, auth_headers: dict[str, str]) -> None:
    client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    )
    envelope = client.post(
        "/api/admin/accounts/export",
        headers=auth_headers,
        json={"password": "long-pass-1"},
    ).json()
    failed = client.post(
        "/api/admin/accounts/import",
        headers=auth_headers,
        json={"password": "long-pass-2", "payload": envelope},
    )
    assert failed.status_code == 400

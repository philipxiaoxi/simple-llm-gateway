from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.services.header_spoof import (
    GROK_CLI_VERSION,
    OPENCODE_USER_AGENT,
    default_header_spoof,
    normalize_header_spoof,
    spoof_headers,
)


class FakeResponse:
    def model_dump(self) -> dict:
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


def _make_key(client: TestClient, auth_headers: dict[str, str], header_spoof: str) -> tuple[str, dict]:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={
            "name": "DS",
            "provider": "deepseek",
            "api_key": "sk-up",
            "header_spoof": header_spoof,
        },
    ).json()
    key = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account["id"]},
    ).json()["key"]
    return key, account


def test_normalize_and_defaults() -> None:
    assert normalize_header_spoof(None) == "none"
    assert normalize_header_spoof(" GROK ") == "grok"
    assert default_header_spoof("grok") == "grok"
    assert default_header_spoof("opencode_go") == "opencode"
    assert default_header_spoof("deepseek") == "none"


def test_spoof_headers_none_is_empty() -> None:
    assert spoof_headers("none") == {}
    assert spoof_headers(None) == {}


def test_spoof_headers_grok_matches_cli() -> None:
    headers = spoof_headers("grok", model="grok-4.6")
    assert headers["User-Agent"].startswith(f"grok-shell/{GROK_CLI_VERSION} (")
    assert headers["x-xai-token-auth"] == "xai-grok-cli"
    assert headers["x-authenticateresponse"] == "authenticate-response"
    assert headers["x-grok-client-identifier"] == "grok-shell"
    assert headers["x-grok-client-version"] == GROK_CLI_VERSION
    assert headers["x-grok-client-mode"] == "headless"
    assert headers["x-grok-model-override"] == "grok-4.6"
    assert headers["x-grok-session-id"]
    assert headers["x-grok-conv-id"] == headers["x-grok-session-id"]
    assert headers["x-grok-req-id"]
    assert headers["x-grok-agent-id"]


def test_spoof_headers_opencode_matches_cli() -> None:
    headers = spoof_headers("opencode")
    assert headers["User-Agent"] == OPENCODE_USER_AGENT
    assert headers["x-opencode-client"] == "cli"
    assert headers["x-opencode-project"] == "global"
    assert headers["x-opencode-session"].startswith("ses_")
    assert headers["x-opencode-request"].startswith("msg_")


def test_create_account_uses_provider_default(client: TestClient, auth_headers: dict[str, str]) -> None:
    grok = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok"},
    )
    assert grok.status_code == 200
    assert grok.json()["header_spoof"] == "grok"

    opencode = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "OC", "provider": "opencode_go", "api_key": "sk-up"},
    )
    assert opencode.json()["header_spoof"] == "opencode"

    deepseek = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    )
    assert deepseek.json()["header_spoof"] == "none"


def test_create_and_update_header_spoof(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok", "header_spoof": "none"},
    )
    assert created.status_code == 200
    account_id = created.json()["id"]
    assert created.json()["header_spoof"] == "none"

    updated = client.patch(
        f"/api/admin/accounts/{account_id}",
        headers=auth_headers,
        json={"header_spoof": "opencode"},
    )
    assert updated.status_code == 200
    assert updated.json()["header_spoof"] == "opencode"


def test_invalid_header_spoof_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "header_spoof": "claude"},
    )
    assert response.status_code == 422


def test_forward_sends_grok_spoof_headers(client: TestClient, auth_headers: dict[str, str]) -> None:
    key, _account = _make_key(client, auth_headers, "grok")
    captured: dict = {}

    async def fake_completion(**kwargs):
        captured.update(kwargs)
        return FakeResponse()

    with patch("app.providers.base.litellm.acompletion", new=fake_completion):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200
    headers = captured["extra_headers"]
    assert headers["x-grok-client-identifier"] == "grok-shell"
    assert headers["x-grok-client-version"] == GROK_CLI_VERSION
    assert headers["x-grok-model-override"] == "deepseek-chat"
    assert headers["Authorization"] == "Bearer sk-up"


def test_forward_sends_opencode_spoof_headers(client: TestClient, auth_headers: dict[str, str]) -> None:
    key, _account = _make_key(client, auth_headers, "opencode")
    captured: dict = {}

    async def fake_completion(**kwargs):
        captured.update(kwargs)
        return FakeResponse()

    with patch("app.providers.base.litellm.acompletion", new=fake_completion):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200
    headers = captured["extra_headers"]
    assert headers["User-Agent"] == OPENCODE_USER_AGENT
    assert headers["x-opencode-client"] == "cli"
    assert headers["x-opencode-session"].startswith("ses_")
    assert "x-grok-client-identifier" not in headers


def test_forward_without_spoof_keeps_plain_auth(client: TestClient, auth_headers: dict[str, str]) -> None:
    key, _account = _make_key(client, auth_headers, "none")
    captured: dict = {}

    async def fake_completion(**kwargs):
        captured.update(kwargs)
        return FakeResponse()

    with patch("app.providers.base.litellm.acompletion", new=fake_completion):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200
    headers = captured["extra_headers"]
    assert headers.get("Authorization") == "Bearer sk-up"
    assert "x-grok-client-identifier" not in headers
    assert "x-opencode-client" not in headers


def test_grok_quota_uses_account_spoof(client: TestClient, auth_headers: dict[str, str]) -> None:
    import asyncio

    from app.db import get_session_factory
    from app.models import UpstreamAccount
    from app.providers.grok import GrokProvider

    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok", "header_spoof": "grok"},
    ).json()
    session = get_session_factory()()
    try:
        row = session.get(UpstreamAccount, account["id"])
        assert row is not None
        captured: dict = {}

        class FakeHttpResponse:
            status_code = 200

            def json(self) -> dict:
                return {"config": {"creditUsagePercent": 12, "currentPeriod": {"end": "2099-01-01T00:00:00Z"}}}

        instance = AsyncMock()
        instance.get = AsyncMock(side_effect=lambda url, headers: captured.update({"headers": headers}) or FakeHttpResponse())
        instance.__aenter__.return_value = instance
        with patch("app.providers.grok.httpx.AsyncClient", return_value=instance):
            result = asyncio.run(GrokProvider().load_quota(row, "oauth-token"))
        assert result.ok is True
        assert captured["headers"]["x-grok-client-version"] == GROK_CLI_VERSION
        assert captured["headers"]["User-Agent"].startswith("grok-shell/")
    finally:
        session.close()

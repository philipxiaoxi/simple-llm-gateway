import re
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.services.conversation import conversation_fingerprint, extract_session_key
from app.services.header_spoof import (
    GROK_CLI_VERSION,
    OPENCODE_USER_AGENT,
    default_header_spoof,
    normalize_header_spoof,
    spoof_headers,
    spoof_identity,
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

        class FakeUserResponse:
            status_code = 200

            def json(self) -> dict:
                return {"userId": "user-1"}

        class FakeHttpResponse:
            status_code = 200

            def json(self) -> dict:
                return {"config": {"creditUsagePercent": 12, "currentPeriod": {"end": "2099-01-01T00:00:00Z"}}}

        def fake_get(url: str, headers: dict) -> object:
            captured["headers"] = headers
            return FakeUserResponse() if url.endswith("/v1/user") else FakeHttpResponse()

        instance = AsyncMock()
        instance.get = AsyncMock(side_effect=fake_get)
        instance.__aenter__.return_value = instance
        with patch("app.providers.grok.httpx.AsyncClient", return_value=instance):
            result = asyncio.run(GrokProvider().load_quota(row, "oauth-token"))
        assert result.ok is True
        assert captured["headers"]["x-grok-client-version"] == GROK_CLI_VERSION
        assert captured["headers"]["User-Agent"].startswith("grok-shell/")
    finally:
        session.close()


SES_RE = re.compile(r"^ses_[0-9a-f]{64}$")


def test_opencode_session_is_stable_within_one_identity() -> None:
    """上游按会话路由/缓存：同会话的每个请求必须带同一个 ses_ id。"""
    with spoof_identity("conv-abc", "api:7:acct:19"):
        first = spoof_headers("opencode")
        second = spoof_headers("opencode")
    assert first["x-opencode-session"] == second["x-opencode-session"]
    assert SES_RE.match(first["x-opencode-session"])
    # 请求 id 每请求唯一才是正确语义
    assert first["x-opencode-request"] != second["x-opencode-request"]


def test_opencode_session_differs_between_sessions() -> None:
    with spoof_identity("conv-abc", "seed"):
        one = spoof_headers("opencode")["x-opencode-session"]
    with spoof_identity("conv-xyz", "seed"):
        two = spoof_headers("opencode")["x-opencode-session"]
    assert one != two


def test_opencode_session_passthrough_when_client_id_is_valid() -> None:
    client_id = "ses_" + "ab12" * 16
    with spoof_identity(client_id, "seed"):
        assert spoof_headers("opencode")["x-opencode-session"] == client_id


def test_opencode_session_falls_back_to_seed_without_client_id() -> None:
    with spoof_identity(None, "api:7:acct:19"):
        first = spoof_headers("opencode")["x-opencode-session"]
        second = spoof_headers("opencode")["x-opencode-session"]
    assert first == second
    assert SES_RE.match(first)


def test_opencode_uses_account_seed_outside_request_context() -> None:
    """技能分类 / 资讯 AI 这类内部调用没有请求上下文，也应是稳定值而非随机。"""
    first = spoof_headers("opencode", seed="account:19")["x-opencode-session"]
    second = spoof_headers("opencode", seed="account:19")["x-opencode-session"]
    assert first == second


def test_grok_ids_are_stable_but_request_id_is_not() -> None:
    with spoof_identity("conv-abc", "api:7:acct:9"):
        one = spoof_headers("grok")
        two = spoof_headers("grok")
    assert one["x-grok-session-id"] == two["x-grok-session-id"]
    assert one["x-grok-conv-id"] == one["x-grok-session-id"]
    assert one["x-grok-agent-id"] == two["x-grok-agent-id"]
    assert one["x-grok-req-id"] != two["x-grok-req-id"]


def test_grok_passes_through_client_uuid_session() -> None:
    client_uuid = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
    with spoof_identity(client_uuid, "seed"):
        assert spoof_headers("grok")["x-grok-session-id"] == client_uuid


def test_session_header_names_include_upstream_session_headers() -> None:
    assert extract_session_key(None, {"X-Opencode-Session": "ses_" + "0" * 64}) == "ses_" + "0" * 64
    assert extract_session_key(None, {"X-Grok-Session-Id": "abc"}) == "abc"


def test_conversation_fingerprint_is_stable_and_discriminating() -> None:
    first = {"messages": [{"role": "user", "content": "帮我读一下这个文件"}]}
    same = {
        "messages": [
            {"role": "user", "content": "帮我读一下这个文件"},
            {"role": "assistant", "content": "好的"},
        ]
    }
    other = {"messages": [{"role": "user", "content": "换一个完全不同的问题"}]}
    assert conversation_fingerprint(first) == conversation_fingerprint(same)
    assert conversation_fingerprint(first) != conversation_fingerprint(other)
    assert conversation_fingerprint(None) is None


def test_forward_opencode_session_is_stable_per_conversation(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    key, _account = _make_key(client, auth_headers, "opencode")
    sessions: list[str] = []

    async def fake_completion(**kwargs):
        sessions.append(kwargs["extra_headers"]["x-opencode-session"])
        return FakeResponse()

    conversation_a = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "固定开场"}]}
    conversation_b = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "另一个开场"}]}
    with patch("app.providers.base.litellm.acompletion", new=fake_completion):
        for payload in (conversation_a, conversation_a, conversation_b):
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
            assert response.status_code == 200

    assert len(sessions) == 3
    assert sessions[0] == sessions[1]
    assert sessions[0] != sessions[2]
    assert SES_RE.match(sessions[0])


def test_forward_grok_session_is_stable_per_conversation(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    key, _account = _make_key(client, auth_headers, "grok")
    captured: list[dict] = []

    async def fake_completion(**kwargs):
        captured.append(kwargs["extra_headers"])
        return FakeResponse()

    payload_a = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "固定开场"}]}
    payload_b = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "另一个开场"}]}
    with patch("app.providers.base.litellm.acompletion", new=fake_completion):
        for payload in (payload_a, payload_a, payload_b):
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
            assert response.status_code == 200

    assert len(captured) == 3
    assert captured[0]["x-grok-session-id"] == captured[1]["x-grok-session-id"]
    assert captured[0]["x-grok-session-id"] != captured[2]["x-grok-session-id"]
    assert captured[0]["x-grok-req-id"] != captured[1]["x-grok-req-id"]


def test_claude_code_persona_headers() -> None:
    with spoof_identity("conv-abc", "api:7:acct:3"):
        headers = spoof_headers("claude_code")
    assert headers["User-Agent"].startswith("claude-cli/")
    assert headers["x-app"] == "cli"
    assert "claude-code-20250219" in headers["anthropic-beta"]
    first = headers["X-Claude-Code-Session-Id"]
    with spoof_identity("conv-abc", "api:7:acct:3"):
        assert spoof_headers("claude_code")["X-Claude-Code-Session-Id"] == first
    with spoof_identity("conv-xyz", "api:7:acct:3"):
        assert spoof_headers("claude_code")["X-Claude-Code-Session-Id"] != first


def test_claude_code_passes_through_client_session_uuid() -> None:
    client_uuid = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
    with spoof_identity(client_uuid, "seed"):
        assert spoof_headers("claude_code")["X-Claude-Code-Session-Id"] == client_uuid


def test_codex_persona_headers_are_stable() -> None:
    with spoof_identity("conv-abc", "api:7:acct:3"):
        headers = spoof_headers("codex")
    assert headers["originator"] == "codex_cli_rs"
    assert headers["User-Agent"].startswith("codex_cli_rs/")
    assert headers["session-id"] == headers["thread-id"] == headers["x-client-request-id"]
    with spoof_identity("conv-abc", "api:7:acct:3"):
        assert spoof_headers("codex")["session-id"] == headers["session-id"]
    with spoof_identity("conv-xyz", "api:7:acct:3"):
        assert spoof_headers("codex")["session-id"] != headers["session-id"]


def test_normalize_accepts_new_personas() -> None:
    assert normalize_header_spoof("claude_code") == "claude_code"
    assert normalize_header_spoof(" CODEX ") == "codex"


def test_session_headers_cover_claude_code_and_codex() -> None:
    assert extract_session_key(None, {"X-Claude-Code-Session-Id": "cc-1"}) == "cc-1"
    assert extract_session_key(None, {"session-id": "codex-1"}) == "codex-1"
    assert extract_session_key(None, {"x-client-request-id": "req-1"}) == "req-1"


def test_turns_with_session_header_merge_into_one_log(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """带会话标识的多轮请求合并成一条记录；不同会话标识各自独立。"""
    key, _account = _make_key(client, auth_headers, "none")

    async def fake_completion(**kwargs):
        return FakeResponse()

    session_one = {"Authorization": f"Bearer {key}", "X-Session-Id": "sess-merge-1"}
    session_two = {"Authorization": f"Bearer {key}", "X-Session-Id": "sess-merge-2"}
    turn_one = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "开场白"}]}
    turn_two = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "开场白"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "继续"},
        ],
    }
    other_session = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "另一个会话"}]}
    with patch("app.providers.base.litellm.acompletion", new=fake_completion):
        for headers, payload in ((session_one, turn_one), (session_one, turn_two), (session_two, other_session)):
            response = client.post("/v1/chat/completions", headers=headers, json=payload)
            assert response.status_code == 200

    logs = client.get("/api/admin/logs", headers=auth_headers).json()["items"]
    assert len(logs) == 2

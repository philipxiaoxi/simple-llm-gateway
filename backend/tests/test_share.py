from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_session_factory
from app.models import RequestLog, UpstreamAccount
from app.routers.local_agent import _sync_agent


def _make_key(client: TestClient, auth_headers: dict[str, str], name: str = "同事A") -> dict:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    ).json()
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": name, "account_id": account["id"]},
    ).json()
    client.post(f"/api/admin/accounts/{account['id']}/models", headers=auth_headers)
    return created


def test_share_lookup_requires_full_key(client: TestClient) -> None:
    response = client.post("/api/share/lookup", json={"api_key": "sk"})
    assert response.status_code == 400


def test_share_lookup_unknown_key(client: TestClient) -> None:
    response = client.post("/api/share/lookup", json={"api_key": "sk-not-exist-xxxxx"})
    assert response.status_code == 404


def test_share_lookup_includes_risk_for_each_bound_account(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    first_account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Official", "provider": "deepseek", "api_key": "sk-official", "risk_level": "low"},
    ).json()
    second_account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Relay", "provider": "grok", "api_key": "sk-relay", "risk_level": "high"},
    ).json()
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "多个上游", "account_ids": [first_account["id"], second_account["id"]]},
    ).json()

    response = client.post("/api/share/lookup", json={"api_key": created["key"]})

    assert response.status_code == 200
    assert response.json()["accounts"] == [
        {
            "id": first_account["id"],
            "name": "Official",
            "source": "upstream",
            "provider": "deepseek",
            "status": "active",
            "risk_level": "low",
            "model_prefix": "Official",
        },
        {
            "id": second_account["id"],
            "name": "Relay",
            "source": "upstream",
            "provider": "grok",
            "status": "active",
            "risk_level": "high",
            "model_prefix": "Relay",
        },
    ]


def test_share_lookup_marks_offline_agent_and_hides_its_models(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _sync_agent("macbook-studio", {"deepseek-local": {"id": "deepseek-local", "name": "DeepSeek", "provider": "deepseek"}})
    session = get_session_factory()()
    try:
        account = session.scalar(select(UpstreamAccount).where(UpstreamAccount.agent_route_id == "deepseek-local"))
        assert account is not None
        account.models_json = '["deepseek-chat"]'
        session.commit()
        account_id = account.id
    finally:
        session.close()
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "本地路由", "account_id": account_id},
    ).json()

    response = client.post("/api/share/lookup", json={"api_key": created["key"]})

    assert response.status_code == 200
    assert response.json()["accounts"][0]["status"] == "offline"
    assert response.json()["models"] == []
    assert response.json()["model_entries"] == []


def test_share_lookup_and_cc_switch_without_admin(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    from unittest.mock import AsyncMock, patch

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
        created = _make_key(client, auth_headers)

    lookup = client.post("/api/share/lookup", json={"api_key": created["key"]})
    assert lookup.status_code == 200
    body = lookup.json()
    assert body["name"] == "同事A"
    assert body["account_name"] == "DS"
    assert body["account_source"] == "upstream"
    assert body["provider"] == "deepseek"
    assert body["today_tokens"] == 0
    assert body["total_tokens"] == 0
    assert body["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert body["model_entries"] == [
        {
            "id": "deepseek-chat",
            "raw_id": "deepseek-chat",
            "account_id": body["accounts"][0]["id"],
            "account_name": "DS",
            "account_source": "upstream",
            "provider": "deepseek",
            "account_index": 0,
        },
        {
            "id": "deepseek-reasoner",
            "raw_id": "deepseek-reasoner",
            "account_id": body["accounts"][0]["id"],
            "account_name": "DS",
            "account_source": "upstream",
            "provider": "deepseek",
            "account_index": 0,
        },
    ]
    assert body["gateway"]["anthropic_base_url"] == "http://testserver"
    assert body["gateway"]["openai_base_url"] == "http://testserver/v1"
    assert "api_key" not in body
    assert {item["app"] for item in body["targets"]} == {"claude", "opencode", "codex", "grokbuild"}

    built = client.post(
        "/api/share/cc-switch",
        json={"api_key": created["key"], "app": "claude", "model": "deepseek-reasoner"},
    )
    assert built.status_code == 200
    query = parse_qs(urlparse(built.json()["url"]).query)
    assert query["app"] == ["claude"]
    assert query["apiKey"] == [created["key"]]
    assert query["model"] == ["deepseek-reasoner"]
    assert not query["endpoint"][0].endswith("/v1")


def test_share_cc_switch_rejects_disabled_key(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = _make_key(client, auth_headers)
    client.patch(
        f"/api/admin/keys/{created['id']}",
        headers=auth_headers,
        json={"status": "disabled"},
    )
    lookup = client.post("/api/share/lookup", json={"api_key": created["key"]})
    assert lookup.status_code == 200
    assert lookup.json()["status"] == "disabled"
    built = client.post(
        "/api/share/cc-switch",
        json={"api_key": created["key"], "app": "opencode", "model": "deepseek-chat"},
    )
    assert built.status_code == 403


def _add_log(api_key_id: int, account_id: int, total_tokens: int, created_at: datetime) -> None:
    session = get_session_factory()()
    try:
        session.add(
            RequestLog(
                account_id=account_id,
                api_key_id=api_key_id,
                protocol="openai",
                model="deepseek-chat",
                stream=False,
                status="ok",
                http_status=200,
                total_tokens=total_tokens,
                latency_ms=1,
                created_at=created_at,
            )
        )
        session.commit()
    finally:
        session.close()


def test_share_lookup_tokens_are_per_key(client: TestClient, auth_headers: dict[str, str]) -> None:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    ).json()
    first = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "同事A", "account_id": account["id"]},
    ).json()
    second = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "同事B", "account_id": account["id"]},
    ).json()
    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)
    _add_log(first["id"], first["account_id"], 10, now)
    _add_log(first["id"], first["account_id"], 20, yesterday)
    _add_log(second["id"], first["account_id"], 99, now)

    mine = client.post("/api/share/lookup", json={"api_key": first["key"]}).json()
    other = client.post("/api/share/lookup", json={"api_key": second["key"]}).json()
    assert mine["today_tokens"] == 10
    assert mine["total_tokens"] == 30
    assert other["today_tokens"] == 99
    assert other["total_tokens"] == 99


def test_share_lookup_hides_disabled_models(client: TestClient, auth_headers: dict[str, str]) -> None:
    from unittest.mock import AsyncMock, patch

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
        created = _make_key(client, auth_headers)

    account_id = created["account_id"]
    client.patch(
        f"/api/admin/accounts/{account_id}/models/deepseek-chat",
        headers=auth_headers,
        json={"enabled": False},
    )
    lookup = client.post("/api/share/lookup", json={"api_key": created["key"]})
    assert lookup.status_code == 200
    assert lookup.json()["models"] == ["deepseek-reasoner"]
    assert [entry["id"] for entry in lookup.json()["model_entries"]] == ["deepseek-reasoner"]

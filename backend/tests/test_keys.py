from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.db import get_session_factory
from app.models import ApiKey, RequestLog


def _account(client: TestClient, auth_headers: dict[str, str]) -> int:
    response = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    )
    return response.json()["id"]


def test_create_key_returns_plaintext_and_can_reveal_later(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    account_id = _account(client, auth_headers)
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "同事A", "account_id": account_id},
    )
    assert created.status_code == 200
    assert created.json()["account_source"] == "upstream"
    plaintext = created.json()["key"]
    assert plaintext.startswith("sk-")
    key_id = created.json()["id"]

    listed = client.get("/api/admin/keys", headers=auth_headers)
    assert listed.json()[0]["key"] is None

    detail = client.get(f"/api/admin/keys/{key_id}", headers=auth_headers)
    assert detail.json()["key"] == plaintext


def test_reveal_key_returns_400_when_secret_cannot_decrypt(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    account_id = _account(client, auth_headers)
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "同事A", "account_id": account_id},
    )
    key_id = created.json()["id"]
    session = get_session_factory()()
    try:
        item = session.get(ApiKey, key_id)
        assert item is not None
        item.key_encrypted = "gAAAAABnot-a-valid-fernet-token-xxxxxxxxxxxx"
        session.commit()
    finally:
        session.close()

    detail = client.get(f"/api/admin/keys/{key_id}", headers=auth_headers)
    assert detail.status_code == 400
    assert "无法解密密钥" in detail.json()["detail"]


def test_cc_switch_links(client: TestClient, auth_headers: dict[str, str]) -> None:
    from unittest.mock import AsyncMock, patch

    account_id = _account(client, auth_headers)

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
        client.post(f"/api/admin/accounts/{account_id}/models", headers=auth_headers)

    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "同事A", "account_id": account_id},
    ).json()
    response = client.get(f"/api/admin/keys/{created['id']}/cc-switch", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["models"] == ["deepseek-chat", "deepseek-reasoner"]
    targets = {item["app"]: item for item in body["targets"]}
    assert targets["opencode"]["needs_dialog"] is True
    assert "url" not in targets["opencode"]
    assert targets["claude"]["needs_dialog"] is True

    built = client.post(
        f"/api/admin/keys/{created['id']}/cc-switch",
        headers=auth_headers,
        json={"app": "claude", "model": "deepseek-reasoner", "haiku_model": "deepseek-chat"},
    )
    assert built.status_code == 200
    assert "model=deepseek-reasoner" in built.json()["url"]
    assert "haikuModel=deepseek-chat" in built.json()["url"]

    opencode = client.post(
        f"/api/admin/keys/{created['id']}/cc-switch",
        headers=auth_headers,
        json={"app": "opencode", "model": "deepseek-reasoner"},
    )
    assert opencode.status_code == 200
    assert "model=deepseek-reasoner" in opencode.json()["url"]
    assert "app=opencode" in opencode.json()["url"]


def test_disable_key(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account_id},
    )
    key_id = created.json()["id"]
    updated = client.patch(f"/api/admin/keys/{key_id}", headers=auth_headers, json={"status": "disabled"})
    assert updated.json()["status"] == "disabled"


def test_delete_key_keeps_request_logs(client: TestClient, auth_headers: dict[str, str]) -> None:
    from unittest.mock import AsyncMock, patch

    account_id = _account(client, auth_headers)
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account_id},
    )
    key_id = created.json()["id"]
    plaintext = created.json()["key"]

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
            headers={"Authorization": f"Bearer {plaintext}"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert proxied.status_code == 200
    logs = client.get("/api/admin/logs", headers=auth_headers)
    assert logs.json()["total"] == 1
    log_id = logs.json()["items"][0]["id"]

    deleted = client.delete(f"/api/admin/keys/{key_id}", headers=auth_headers)
    assert deleted.status_code == 200
    listed = client.get("/api/admin/keys", headers=auth_headers)
    assert listed.json() == []
    remaining_logs = client.get("/api/admin/logs", headers=auth_headers)
    assert remaining_logs.json()["total"] == 1
    leftover = remaining_logs.json()["items"][0]
    assert leftover["api_key_id"] == key_id
    assert leftover["api_key_name"] == "k"
    assert leftover["account_name"] == "DS"
    messages = client.get(f"/api/admin/logs/{log_id}/messages", headers=auth_headers)
    assert messages.status_code == 200
    assert messages.json()["total"] >= 1


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


def _set_last_used(key_id: int, last_used_at: datetime | None) -> None:
    session = get_session_factory()()
    try:
        item = session.get(ApiKey, key_id)
        assert item is not None
        item.last_used_at = last_used_at
        session.commit()
    finally:
        session.close()


def test_list_keys_includes_token_usage_and_sorts(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    account_id = _account(client, auth_headers)
    older = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "先建", "account_id": account_id},
    ).json()
    newer = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "后建", "account_id": account_id},
    ).json()
    unused = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "未用", "account_id": account_id},
    ).json()

    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)
    _add_log(older["id"], account_id, 10, now)
    _add_log(older["id"], account_id, 20, yesterday)
    _add_log(newer["id"], account_id, 99, now)
    _set_last_used(older["id"], now)
    _set_last_used(newer["id"], yesterday)
    _set_last_used(unused["id"], None)

    listed = client.get("/api/admin/keys", headers=auth_headers)
    assert listed.status_code == 200
    default_names = [item["name"] for item in listed.json()]
    assert default_names == ["先建", "后建", "未用"]
    by_name = {item["name"]: item for item in listed.json()}
    assert by_name["先建"]["today_tokens"] == 10
    assert by_name["先建"]["total_tokens"] == 30
    assert by_name["后建"]["today_tokens"] == 99
    assert by_name["后建"]["total_tokens"] == 99
    assert by_name["未用"]["today_tokens"] == 0
    assert by_name["未用"]["total_tokens"] == 0

    by_tokens = client.get("/api/admin/keys?sort=tokens", headers=auth_headers)
    assert [item["name"] for item in by_tokens.json()] == ["后建", "先建", "未用"]

    by_used = client.get("/api/admin/keys?sort=last_used", headers=auth_headers)
    assert [item["name"] for item in by_used.json()] == ["先建", "后建", "未用"]

    by_created = client.get("/api/admin/keys?sort=created_at", headers=auth_headers)
    assert [item["name"] for item in by_created.json()] == ["未用", "后建", "先建"]

    detail = client.get(f"/api/admin/keys/{older['id']}", headers=auth_headers)
    assert detail.json()["today_tokens"] == 10
    assert detail.json()["total_tokens"] == 30

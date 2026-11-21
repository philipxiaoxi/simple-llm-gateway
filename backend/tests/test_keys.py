from datetime import datetime, timedelta
from time import perf_counter

import pytest
from fastapi.testclient import TestClient

from app.clock import utcnow
from app.db import get_session_factory
from app.models import ApiKey, RequestLog
from app.services.dashboard_stats import ensure_daily_rollups
from app.services.proxy import _finalize_request_log


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
    vscode_models = {item["id"]: item for item in body["vscode"]["models"]}
    assert vscode_models["deepseek-chat"]["maxInputTokens"] == 128000
    assert vscode_models["deepseek-reasoner"]["maxOutputTokens"] == 16000
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
    detail = client.get(f"/api/admin/keys/{key_id}", headers=auth_headers)
    assert detail.json()["status"] == "disabled"


def test_update_key_replaces_bound_accounts(client: TestClient, auth_headers: dict[str, str]) -> None:
    first = _account(client, auth_headers)
    second = _account(client, auth_headers)
    third = _account(client, auth_headers)
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "多账号", "account_ids": [first, second]},
    ).json()

    updated = client.patch(
        f"/api/admin/keys/{created['id']}",
        headers=auth_headers,
        json={"account_ids": [second, third]},
    )
    assert updated.status_code == 200
    assert updated.json()["account_ids"] == [second, third]
    assert updated.json()["account_id"] == second

    detail = client.get(f"/api/admin/keys/{created['id']}", headers=auth_headers)
    assert detail.json()["account_ids"] == [second, third]
    assert detail.json()["account_id"] == second

    reordered = client.patch(
        f"/api/admin/keys/{created['id']}",
        headers=auth_headers,
        json={"account_ids": [third, second]},
    )
    assert reordered.status_code == 200
    assert reordered.json()["account_ids"] == [third, second]
    assert reordered.json()["account_id"] == third


def test_key_log_content_toggle_persists(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "隐私 Key", "account_id": account_id, "log_content_enabled": False},
    ).json()
    assert created["log_content_enabled"] is False

    updated = client.patch(
        f"/api/admin/keys/{created['id']}",
        headers=auth_headers,
        json={"log_content_enabled": True},
    )
    assert updated.status_code == 200
    assert updated.json()["log_content_enabled"] is True

    detail = client.get(f"/api/admin/keys/{created['id']}", headers=auth_headers)
    assert detail.json()["log_content_enabled"] is True


def _finalize_sample_log(
    account_id: int,
    api_key_id: int,
    *,
    session_id: str,
    followup: bool = False,
) -> None:
    messages = [{"role": "user", "content": "secret prompt"}]
    answer = "secret answer"
    reasoning = {"call-1": "secret reasoning"}
    if followup:
        messages.extend([
            {"role": "assistant", "content": answer},
            {"role": "user", "content": "second prompt"},
        ])
        answer = "second answer"
        reasoning = {"call-2": "second reasoning"}
    _finalize_request_log(
        account_id=account_id,
        api_key_id=api_key_id,
        protocol="openai",
        model="deepseek-chat",
        stream=False,
        status="success",
        http_status=200,
        error_text=None,
        usage=(3, 4, 7),
        started=perf_counter(),
        request_body={"messages": messages},
        response_body={"choices": [{"message": {"role": "assistant", "content": answer}}]},
        request_headers={"Authorization": "Bearer secret", "x-session-id": session_id},
        reasoning_map=reasoning,
    )


@pytest.mark.parametrize("request_content_capture", [True, False], indirect=True)
@pytest.mark.parametrize("key_capture", [True, False])
def test_request_log_capture_requires_global_and_key_opt_in(
    client: TestClient, auth_headers: dict[str, str], wait_for_log_content, request_content_capture: bool, key_capture: bool
) -> None:
    account_id = _account(client, auth_headers)
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "capture", "account_id": account_id, "log_content_enabled": key_capture},
    ).json()
    assert created["log_content_enabled"] is key_capture

    _finalize_sample_log(account_id, created["id"], session_id="capture-session")
    wait_for_log_content()
    log_id = client.get("/api/admin/logs", headers=auth_headers).json()["items"][0]["id"]
    log = client.get(f"/api/admin/logs/{log_id}", headers=auth_headers).json()
    exported = client.get(f"/api/admin/logs/{log_id}/export", headers=auth_headers).json()

    assert log["prompt_tokens"] == 3
    assert log["completion_tokens"] == 4
    assert log["total_tokens"] == 7
    if request_content_capture and key_capture:
        assert log["request_headers"]["x-session-id"] == "capture-session"
        assert [item["content"] for item in exported["messages"]] == ["secret prompt", "secret answer"]
        assert exported["reasoning"] == {"call-1": "secret reasoning"}
    else:
        assert log["request_headers"] is None
        assert exported == {"messages": [], "reasoning": {}}


@pytest.mark.parametrize("request_content_capture", [True], indirect=True)
def test_disabling_content_stops_appending_to_existing_session(
    client: TestClient, auth_headers: dict[str, str], wait_for_log_content
) -> None:
    account_id = _account(client, auth_headers)
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "中途关闭", "account_id": account_id},
    ).json()
    assert created["log_content_enabled"] is True

    _finalize_sample_log(account_id, created["id"], session_id="switch-session")
    wait_for_log_content()
    log_id = client.get("/api/admin/logs", headers=auth_headers).json()["items"][0]["id"]
    updated = client.patch(
        f"/api/admin/keys/{created['id']}", headers=auth_headers, json={"log_content_enabled": False}
    )
    assert updated.status_code == 200
    _finalize_sample_log(account_id, created["id"], session_id="switch-session", followup=True)
    wait_for_log_content()

    logs = client.get("/api/admin/logs", headers=auth_headers).json()
    assert [item["id"] for item in logs["items"]] == [log_id]
    detail = client.get(f"/api/admin/logs/{log_id}", headers=auth_headers).json()
    assert detail["turn_count"] == 2
    assert detail["total_tokens"] == 14
    exported = client.get(f"/api/admin/logs/{log_id}/export", headers=auth_headers).json()
    assert [item["content"] for item in exported["messages"]] == ["secret prompt", "secret answer"]
    assert exported["reasoning"] == {"call-1": "secret reasoning"}


@pytest.mark.parametrize("request_content_capture", [True], indirect=True)
def test_delete_key_keeps_request_logs(client: TestClient, auth_headers: dict[str, str], wait_for_log_content) -> None:
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
    wait_for_log_content()
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
    assert messages.json()["total"] == 2


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

    now = utcnow()
    yesterday = now - timedelta(days=1)
    _add_log(older["id"], account_id, 10, now)
    _add_log(older["id"], account_id, 20, yesterday)
    _add_log(newer["id"], account_id, 99, now)
    with get_session_factory()() as session:
        ensure_daily_rollups(session)
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


def test_key_rate_limit_defaults_and_update(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "限速默认", "account_id": account_id},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["rpm_limit"] == 400
    assert body["concurrency_limit"] == 200
    key_id = body["id"]

    updated = client.patch(
        f"/api/admin/keys/{key_id}",
        headers=auth_headers,
        json={"rpm_limit": 60, "concurrency_limit": 5},
    )
    assert updated.status_code == 200
    assert updated.json()["rpm_limit"] == 60
    assert updated.json()["concurrency_limit"] == 5

    # 0 表示不限，允许保存
    unlimited = client.patch(
        f"/api/admin/keys/{key_id}",
        headers=auth_headers,
        json={"rpm_limit": 0, "concurrency_limit": 0},
    )
    assert unlimited.status_code == 200
    assert unlimited.json()["rpm_limit"] == 0

    usage = client.get("/api/admin/keys/usage", headers=auth_headers)
    assert usage.status_code == 200
    assert "items" in usage.json()

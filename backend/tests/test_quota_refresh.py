import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.clock import utcnow
from app.db import get_session_factory
from app.models import UpstreamAccount
from app.services.quota import accounts_due_for_quota_refresh, refresh_due_quotas


def _create_account(client: TestClient, auth_headers: dict[str, str], name: str) -> dict:
    return client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": name, "provider": "deepseek", "api_key": "sk-up"},
    ).json()


def _set_quota_updated_at(account_id: int, updated_at: datetime | None) -> None:
    session = get_session_factory()()
    try:
        account = session.get(UpstreamAccount, account_id)
        assert account is not None
        account.quota_updated_at = updated_at
        session.commit()
    finally:
        session.close()


def test_accounts_due_for_quota_refresh_skips_fresh_disabled_and_empty(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    stale = _create_account(client, auth_headers, "过期")
    fresh = _create_account(client, auth_headers, "刚刷")
    never = _create_account(client, auth_headers, "从未")
    disabled = _create_account(client, auth_headers, "停用")
    empty = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "无密钥", "provider": "deepseek"},
    ).json()

    now = utcnow()
    _set_quota_updated_at(stale["id"], now - timedelta(hours=2))
    _set_quota_updated_at(fresh["id"], now)
    _set_quota_updated_at(never["id"], None)
    _set_quota_updated_at(disabled["id"], now - timedelta(hours=2))
    client.patch(
        f"/api/admin/accounts/{disabled['id']}",
        headers=auth_headers,
        json={"status": "disabled"},
    )

    session = get_session_factory()()
    try:
        due_ids = {account.id for account in accounts_due_for_quota_refresh(session, now=now)}
        assert due_ids == {stale["id"], never["id"]}
        assert empty["id"] not in due_ids
        assert disabled["id"] not in due_ids
        assert fresh["id"] not in due_ids
    finally:
        session.close()


def test_refresh_due_quotas_writes_stale_account(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    account = _create_account(client, auth_headers, "DS")
    _set_quota_updated_at(account["id"], utcnow() - timedelta(hours=2))

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "1.2"}]}

    with patch("app.providers.deepseek.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=FakeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        refreshed = asyncio.run(refresh_due_quotas())

    assert refreshed == 1
    stored = client.get(f"/api/admin/accounts/{account['id']}", headers=auth_headers).json()
    assert stored["quota"]["ok"] is True
    assert stored["quota"]["items"][0]["value"] == "$1.20"
    assert stored["quota_updated_at"] is not None
    assert client_cls.called

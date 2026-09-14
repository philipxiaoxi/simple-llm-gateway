from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def test_jobs_require_auth(client: TestClient) -> None:
    response = client.get("/api/admin/jobs")
    assert response.status_code == 401


def test_list_jobs_includes_catalog_quota_oauth_leaderboard(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/admin/jobs", headers=auth_headers)
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert ids == ["catalog", "quota", "oauth", "leaderboard", "content_audit"]
    catalog = response.json()["items"][0]
    assert catalog["kind"] == "loop"
    assert catalog["params"][0]["key"] == "interval_seconds"
    assert catalog["params"][0]["value"] == 172800
    leaderboard = response.json()["items"][3]
    assert leaderboard["kind"] == "loop"
    assert {item["key"] for item in leaderboard["params"]} == {"interval_seconds"}
    assert leaderboard["params"][0]["value"] == 43200
    audit = response.json()["items"][4]
    assert audit["kind"] == "manual"
    assert audit["name"] == "内容审计扫描"
    assert audit["params"] == []


def test_update_catalog_interval(client: TestClient, auth_headers: dict[str, str]) -> None:
    too_small = client.patch(
        "/api/admin/jobs/catalog",
        headers=auth_headers,
        json={"interval_seconds": 10},
    )
    assert too_small.status_code == 422
    updated = client.patch(
        "/api/admin/jobs/catalog",
        headers=auth_headers,
        json={"interval_seconds": 86400},
    )
    assert updated.status_code == 200
    catalog = next(item for item in updated.json()["items"] if item["id"] == "catalog")
    assert catalog["params"][0]["value"] == 86400
    listed = client.get("/api/admin/jobs", headers=auth_headers).json()["items"]
    assert next(item for item in listed if item["id"] == "catalog")["params"][0]["value"] == 86400


def test_update_leaderboard_interval(client: TestClient, auth_headers: dict[str, str]) -> None:
    updated = client.patch(
        "/api/admin/jobs/leaderboard",
        headers=auth_headers,
        json={"interval_seconds": 21600},
    )
    assert updated.status_code == 200
    leaderboard = next(item for item in updated.json()["items"] if item["id"] == "leaderboard")
    assert leaderboard["params"][0]["value"] == 21600
    assert leaderboard["kind"] == "loop"


def test_run_catalog_job_fetches_models_dev(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    from app.services import model_caps as model_caps_mod

    payload = {
        "openai": {
            "models": {
                "gpt-4o": {
                    "id": "gpt-4o",
                    "limit": {"context": 128000, "output": 16384},
                    "modalities": {"input": ["text"], "output": ["text"]},
                }
            }
        }
    }
    monkeypatch.setattr(model_caps_mod, "_fetch_models_dev", lambda: payload)
    response = client.post("/api/admin/jobs/catalog/run", headers=auth_headers)
    assert response.status_code == 200, response.text
    catalog = next(item for item in response.json()["items"] if item["id"] == "catalog")
    assert catalog["last_ok"] is True
    assert catalog["details"]["model_count"] == 1
    assert catalog["cache_fetched_at"] is not None


def test_run_leaderboard_job_fetches_aihot(client: TestClient, auth_headers: dict[str, str]) -> None:
    from aihot_payloads import FLIGHT_PAYLOAD

    with patch("app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=FLIGHT_PAYLOAD)):
        response = client.post("/api/admin/jobs/leaderboard/run", headers=auth_headers)
    assert response.status_code == 200, response.text
    leaderboard = next(item for item in response.json()["items"] if item["id"] == "leaderboard")
    assert leaderboard["last_ok"] is True
    assert leaderboard["last_message"] == "已缓存 30 条榜单"
    assert leaderboard["details"]["total"] == 30
    assert leaderboard["details"]["stale"] is False
    assert leaderboard["cache_fetched_at"] is not None
    assert leaderboard["error_message"] is None


def test_run_unknown_job(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post("/api/admin/jobs/missing/run", headers=auth_headers)
    assert response.status_code == 404


async def test_loop_catches_up_stale_leaderboard_cache(client: TestClient, auth_headers: dict[str, str]) -> None:
    from aihot_payloads import FLIGHT_PAYLOAD
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import LeaderboardSnapshot
    from app.services import jobs as jobs_service

    # 没有缓存时，进循环前先补一次，页面不用等满一个间隔
    with patch(
        "app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=FLIGHT_PAYLOAD)
    ) as fetch:
        await jobs_service._catch_up_stale_cache("leaderboard")
    assert fetch.await_count == 1
    session = get_session_factory()()
    try:
        snapshot = session.scalar(select(LeaderboardSnapshot).order_by(LeaderboardSnapshot.id.desc()).limit(1))
        assert snapshot is not None
        assert len(json.loads(snapshot.entries_json)) == 30
    finally:
        session.close()

    # 缓存还新鲜就不该重复拉取
    with patch(
        "app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=FLIGHT_PAYLOAD)
    ) as fetch:
        await jobs_service._catch_up_stale_cache("leaderboard")
    assert fetch.await_count == 0

    # 其他循环任务不受影响
    with patch(
        "app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=FLIGHT_PAYLOAD)
    ) as fetch:
        await jobs_service._catch_up_stale_cache("catalog")
    assert fetch.await_count == 0


def test_run_quota_job_refreshes_due_accounts(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    )
    assert created.status_code == 200

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
        response = client.post("/api/admin/jobs/quota/run", headers=auth_headers)
    assert response.status_code == 200, response.text
    quota_job = next(item for item in response.json()["items"] if item["id"] == "quota")
    assert quota_job["last_ok"] is True
    assert quota_job["details"]["account_count"] == 1

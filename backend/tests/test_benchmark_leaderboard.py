from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.clock import utcnow


def _account(client: TestClient, auth_headers: dict[str, str], name: str = "A", provider: str = "deepseek") -> int:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": name, "provider": provider, "api_key": "sk-up"},
    )
    assert created.status_code == 200, created.text
    return created.json()["id"]


def _add_model(client: TestClient, auth_headers: dict[str, str], account_id: int, model: str) -> None:
    added = client.post(
        f"/api/admin/accounts/{account_id}/models/custom",
        headers=auth_headers,
        json={"id": model},
    )
    assert added.status_code == 200, added.text


def _save(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    account_id: int,
    model: str,
    speed: float | None,
    ok: bool = True,
    account_name: str = "A",
    provider: str = "deepseek",
    first_token_ms: float | None = 100.0,
    total_ms: float | None = 500.0,
) -> None:
    response = client.post(
        "/api/admin/benchmark/history",
        headers=auth_headers,
        json={
            "prompt": "测速",
            "max_tokens": 32,
            "results": [
                {
                    "account_id": account_id,
                    "account_name": account_name,
                    "provider": provider,
                    "model": model,
                    "ok": ok,
                    "timeout": False,
                    "first_token_ms": first_token_ms,
                    "total_ms": total_ms,
                    "output_tokens_per_second": speed,
                }
            ],
        },
    )
    assert response.status_code == 200, response.text


def _items(client: TestClient, auth_headers: dict[str, str]) -> list[dict]:
    response = client.get("/api/admin/benchmark/leaderboard", headers=auth_headers)
    assert response.status_code == 200
    return response.json()["items"]


def test_leaderboard_ranks_by_average_speed(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)
    for model in ("m1", "m2", "m3"):
        _add_model(client, auth_headers, account_id, model)

    _save(client, auth_headers, account_id=account_id, model="m1", speed=10.0)
    _save(client, auth_headers, account_id=account_id, model="m1", speed=30.0)
    _save(client, auth_headers, account_id=account_id, model="m2", speed=50.0)
    _save(client, auth_headers, account_id=account_id, model="m1", speed=999.0, ok=False)
    _save(client, auth_headers, account_id=account_id, model="m3", speed=None)

    items = _items(client, auth_headers)
    assert [item["model"] for item in items] == ["m2", "m1"]
    assert [item["rank"] for item in items] == [1, 2]
    assert items[0]["avg_speed"] == 50.0
    assert items[1]["avg_speed"] == 20.0
    assert items[1]["samples"] == 2
    assert items[1]["best_speed"] == 30.0


def test_leaderboard_splits_same_model_by_account(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_a = _account(client, auth_headers, name="A")
    account_b = _account(client, auth_headers, name="B")
    _add_model(client, auth_headers, account_a, "m1")
    _add_model(client, auth_headers, account_b, "m1")

    _save(client, auth_headers, account_id=account_a, model="m1", speed=10.0, account_name="A")
    _save(client, auth_headers, account_id=account_b, model="m1", speed=80.0, account_name="B")

    items = _items(client, auth_headers)
    assert [item["account_name"] for item in items] == ["B", "A"]
    assert all(item["model"] == "m1" for item in items)


def test_leaderboard_hides_deleted_models_and_accounts(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)
    _add_model(client, auth_headers, account_id, "m1")
    _add_model(client, auth_headers, account_id, "m2")
    _save(client, auth_headers, account_id=account_id, model="m1", speed=10.0)
    _save(client, auth_headers, account_id=account_id, model="m2", speed=20.0)
    assert {item["model"] for item in _items(client, auth_headers)} == {"m1", "m2"}

    removed = client.delete(f"/api/admin/accounts/{account_id}/models/custom/m1", headers=auth_headers)
    assert removed.status_code == 200
    assert {item["model"] for item in _items(client, auth_headers)} == {"m2"}

    client.delete(f"/api/admin/accounts/{account_id}", headers=auth_headers)
    assert _items(client, auth_headers) == []


def test_leaderboard_export_csv(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)
    _add_model(client, auth_headers, account_id, "m1")
    _add_model(client, auth_headers, account_id, "m2")
    _save(client, auth_headers, account_id=account_id, model="m1", speed=10.0)
    _save(client, auth_headers, account_id=account_id, model="m2", speed=50.0)

    response = client.get("/api/admin/benchmark/leaderboard/export", headers=auth_headers)
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    lines = response.content.decode("utf-8-sig").strip().splitlines()
    assert lines[0].startswith("排名,模型,账号")
    assert lines[1].startswith("1,m2")
    assert lines[2].startswith("2,m1")


def test_history_list_include_results(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)
    _add_model(client, auth_headers, account_id, "m1")
    _add_model(client, auth_headers, account_id, "m2")
    _save(client, auth_headers, account_id=account_id, model="m1", speed=12.0)
    _save(client, auth_headers, account_id=account_id, model="m2", speed=34.0)

    without = client.get("/api/admin/benchmark/history", headers=auth_headers).json()["items"]
    assert all("results" not in item for item in without)

    with_results = client.get(
        "/api/admin/benchmark/history?include_results=true", headers=auth_headers
    ).json()["items"]
    assert all(len(item["results"]) == 1 for item in with_results)


def test_leaderboard_only_last_month(client: TestClient, auth_headers: dict[str, str]) -> None:
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import BenchmarkRun

    account_id = _account(client, auth_headers)
    _add_model(client, auth_headers, account_id, "m1")
    _add_model(client, auth_headers, account_id, "m2")
    _save(client, auth_headers, account_id=account_id, model="m1", speed=10.0)
    _save(client, auth_headers, account_id=account_id, model="m2", speed=90.0)

    # 把最近一次（m2）测速回拨到 45 天前，它不应再进入排行榜
    session = get_session_factory()()
    try:
        run = session.scalar(select(BenchmarkRun).order_by(BenchmarkRun.id.desc()).limit(1))
        assert run is not None
        run.created_at = utcnow() - timedelta(days=45)
        session.commit()
    finally:
        session.close()

    assert [item["model"] for item in _items(client, auth_headers)] == ["m1"]

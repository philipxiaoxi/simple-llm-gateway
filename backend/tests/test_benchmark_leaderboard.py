from __future__ import annotations

from fastapi.testclient import TestClient


def _save(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
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
                    "account_id": 1,
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


def test_leaderboard_ranks_by_average_speed(client: TestClient, auth_headers: dict[str, str]) -> None:
    _save(client, auth_headers, model="m1", speed=10.0)
    _save(client, auth_headers, model="m1", speed=30.0)
    _save(client, auth_headers, model="m2", speed=50.0)
    _save(client, auth_headers, model="m1", speed=999.0, ok=False)
    _save(client, auth_headers, model="m3", speed=None)

    response = client.get("/api/admin/benchmark/leaderboard", headers=auth_headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["model"] for item in items] == ["m2", "m1"]
    assert [item["rank"] for item in items] == [1, 2]
    assert items[0]["avg_speed"] == 50.0
    assert items[1]["avg_speed"] == 20.0
    assert items[1]["samples"] == 2
    assert items[1]["best_speed"] == 30.0


def test_leaderboard_splits_same_model_by_account(client: TestClient, auth_headers: dict[str, str]) -> None:
    _save(client, auth_headers, model="m1", speed=10.0, account_name="A")
    _save(client, auth_headers, model="m1", speed=80.0, account_name="B")

    items = client.get("/api/admin/benchmark/leaderboard", headers=auth_headers).json()["items"]
    assert [item["account_name"] for item in items] == ["B", "A"]
    assert all(item["model"] == "m1" for item in items)


def test_leaderboard_export_csv(client: TestClient, auth_headers: dict[str, str]) -> None:
    _save(client, auth_headers, model="m1", speed=10.0)
    _save(client, auth_headers, model="m2", speed=50.0)

    response = client.get("/api/admin/benchmark/leaderboard/export", headers=auth_headers)
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    lines = response.content.decode("utf-8-sig").strip().splitlines()
    assert lines[0].startswith("排名,模型,账号")
    assert lines[1].startswith("1,m2")
    assert lines[2].startswith("2,m1")


def test_history_list_include_results(client: TestClient, auth_headers: dict[str, str]) -> None:
    _save(client, auth_headers, model="m1", speed=12.0)
    _save(client, auth_headers, model="m2", speed=34.0)

    without = client.get("/api/admin/benchmark/history", headers=auth_headers).json()["items"]
    assert all("results" not in item for item in without)

    with_results = client.get(
        "/api/admin/benchmark/history?include_results=true", headers=auth_headers
    ).json()["items"]
    assert all(len(item["results"]) == 1 for item in with_results)

from __future__ import annotations

import threading
import time

import pytest
from app.cache import TTLCache
from fastapi.testclient import TestClient


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_ttl_cache_reuses_value_until_expiry() -> None:
    clock = FakeClock()
    cache: TTLCache[list[int]] = TTLCache("test", ttl=60, clock=clock)
    calls: list[int] = []

    def factory() -> list[int]:
        calls.append(len(calls) + 1)
        return list(calls)

    assert cache.get_or_compute("k", factory) == [1]
    clock.advance(59)
    assert cache.get_or_compute("k", factory) == [1]
    assert cache.hits == 1

    clock.advance(2)
    assert cache.get_or_compute("k", factory) == [1, 2]
    assert cache.get("k") == [1, 2]
    assert cache.stats()["misses"] == 2


def test_ttl_cache_zero_ttl_disables_cache() -> None:
    cache: TTLCache[int] = TTLCache("test", ttl=0)
    calls: list[int] = []

    def factory() -> int:
        calls.append(1)
        return len(calls)

    assert cache.get_or_compute("k", factory) == 1
    assert cache.get_or_compute("k", factory) == 2
    assert cache.get("k") is None


def test_ttl_cache_delete_and_prefix_invalidate() -> None:
    cache: TTLCache[str] = TTLCache("test", ttl=60)
    cache.set("a", "1")
    cache.set("b", "2")
    assert cache.invalidate(lambda key: str(key).startswith("a")) == 1
    assert cache.get("a") is None
    assert cache.get("b") == "2"
    cache.delete("b")
    assert cache.get("b") is None


def test_ttl_cache_evicts_oldest_when_full() -> None:
    clock = FakeClock()
    cache: TTLCache[str] = TTLCache("test", ttl=600, max_entries=2, clock=clock)
    cache.set("a", "1")
    clock.advance(1)
    cache.set("b", "2")
    clock.advance(1)
    cache.set("c", "3")
    assert cache.get("a") is None
    assert cache.get("b") == "2"
    assert cache.get("c") == "3"


def test_ttl_cache_single_flight_merges_concurrent_misses() -> None:
    cache: TTLCache[str] = TTLCache("test", ttl=60)
    barrier = threading.Barrier(4)
    calls: list[int] = []
    lock = threading.Lock()

    def factory() -> str:
        with lock:
            calls.append(1)
        time.sleep(0.05)
        return "value"

    def worker() -> None:
        barrier.wait()
        assert cache.get_or_compute("k", factory) == "value"

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1


def test_dashboard_stats_reads_snapshot_until_refresh(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.routers import admin_dashboard

    calls: list[int] = []
    real = admin_dashboard.compute_stats

    def spy(db, days):  # type: ignore[no-untyped-def]
        calls.append(days)
        return real(db, days)

    monkeypatch.setattr(admin_dashboard, "compute_stats", spy)
    empty = client.get("/api/admin/dashboard/stats", headers=auth_headers, params={"days": 7})
    assert empty.status_code == 200
    assert empty.json()["daily"] == []
    assert calls == []

    forced = client.get("/api/admin/dashboard/stats", headers=auth_headers, params={"days": 7, "refresh": 1})
    assert forced.status_code == 200
    assert calls == [7]
    assert forced.json()["computed_at"]

    cached = client.get("/api/admin/dashboard/stats", headers=auth_headers, params={"days": 7})
    assert cached.status_code == 200
    assert cached.json()["computed_at"] == forced.json()["computed_at"]
    assert calls == [7]


def test_jobs_snapshot_cached_and_invalidated(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import jobs as jobs_service

    calls: list[str] = []
    real = jobs_service._SNAPSHOTTERS["quota"]

    def spy():  # type: ignore[no-untyped-def]
        calls.append("quota")
        return real()

    monkeypatch.setitem(jobs_service._SNAPSHOTTERS, "quota", spy)

    assert client.get("/api/admin/jobs", headers=auth_headers).status_code == 200
    assert client.get("/api/admin/jobs", headers=auth_headers).status_code == 200
    assert calls == ["quota"]

    # 「刷新状态」按钮强制重算
    client.get("/api/admin/jobs", headers=auth_headers, params={"refresh": 1})
    assert calls == ["quota", "quota"]

    # 改任务参数后该任务的快照失效（返回值里也要是新值）
    updated = client.patch(
        "/api/admin/jobs/quota",
        headers=auth_headers,
        json={"interval_seconds": 7200},
    )
    assert updated.status_code == 200
    assert calls == ["quota", "quota", "quota"]
    quota = next(item for item in updated.json()["items"] if item["id"] == "quota")
    assert quota["ttl_seconds"] == 7200


def test_jobs_snapshot_recomputed_after_run(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import jobs as jobs_service

    calls: list[str] = []
    real = jobs_service._SNAPSHOTTERS["catalog"]

    def spy():  # type: ignore[no-untyped-def]
        calls.append("catalog")
        return real()

    monkeypatch.setattr(
        "app.services.model_caps._fetch_models_dev",
        lambda: {"openai": {"models": {"gpt-4o": {"id": "gpt-4o"}}}},
    )
    monkeypatch.setitem(jobs_service._SNAPSHOTTERS, "catalog", spy)

    client.get("/api/admin/jobs", headers=auth_headers)
    client.get("/api/admin/jobs", headers=auth_headers)
    assert calls == ["catalog"]

    response = client.post("/api/admin/jobs/catalog/run", headers=auth_headers)
    assert response.status_code == 200, response.text
    # 执行结束后快照已失效，返回的列表里带本轮结果
    catalog = next(item for item in response.json()["items"] if item["id"] == "catalog")
    assert catalog["last_ok"] is True
    assert catalog["details"]["model_count"] == 1
    assert calls == ["catalog", "catalog"]



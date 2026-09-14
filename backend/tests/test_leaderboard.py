from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from aihot_payloads import FLIGHT_PAYLOAD
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.clock import utcnow
from app.db import get_session_factory
from app.models import LeaderboardSnapshot, UpstreamAccount
from app.routers.local_agent import _sync_agent
from app.services.leaderboard import (
    LeaderboardError,
    canonical_model_key,
    mask_public_label,
    parse_leaderboard_payload,
)

# 当前上游形态：RSC 飞行载荷里渲染出的榜单表格（真实抓取样例见 aihot_payloads.py）
RSC_PAYLOAD = FLIGHT_PAYLOAD
# 旧版形态：内联 {"entries":[...]} 的 JSON，解析仍要兼容
LEGACY_RSC_PAYLOAD = (
    '1:{"entries":['
    '{"rank":1,"previousRank":2,"rankChange":1,"slug":"claude-fable-5","name":"Claude Fable 5",'
    '"provider":"Anthropic","providerSlug":"anthropic","releasedAt":"2026-06-09T00:00:00.000Z",'
    '"contextWindowTokens":1000000,"pricingKind":"TOKEN","pricingOfficialModelId":"claude-fable-5",'
    '"inputPricePerMillionUsd":10,"outputPricePerMillionUsd":50,"inputPricePerMillionCny":67.205,'
    '"outputPricePerMillionCny":336.025,"priceQuote":"USD_CONVERTED","pricingSourceName":"Claude API",'
    '"pricingSourceUrl":"https://example.com","score":89.2,"uncertainty":3.1,"coverage":0.88,'
    '"confidence":"HIGH","possibleRankFrom":1,"possibleRankTo":3,"metricCount":9,'
    '"summary":"by 6 families","components":{"artificial-analysis":{"score":99.6,"coverage":0.3,"metricCount":1}}}'
    ']}'
)


def _find_item(response, slug: str) -> dict:
    payload = response.json()
    item = next((entry for entry in payload["items"] if entry["slug"] == slug), None)
    assert item is not None, f"{slug} 不在榜单里：{[entry['slug'] for entry in payload['items']]}"
    return item


def test_parse_legacy_entries_payload() -> None:
    items = parse_leaderboard_payload(LEGACY_RSC_PAYLOAD)
    assert len(items) == 1
    assert items[0]["slug"] == "claude-fable-5"
    assert items[0]["score"] == 89.2
    assert items[0]["released_at"] == "2026-06-09T00:00:00.000Z"
    assert items[0]["summary"] == "by 6 families"
    assert items[0]["context_window_tokens"] == 1000000
    assert items[0]["components"]["artificial-analysis"]["coverage"] == 0.3
    # 旧版载荷没有缓存价，保持 None
    assert items[0]["cache_input_price_per_million_cny"] is None


def test_parse_flight_leaderboard_payload() -> None:
    items = parse_leaderboard_payload(RSC_PAYLOAD)
    assert len(items) == 30
    assert [item["rank"] for item in items] == list(range(1, 31))
    assert all(item["slug"] and item["name"] for item in items)

    top = items[0]
    assert top["slug"] == "gpt-6-astra"
    assert top["name"] == "GPT-6 Astra"
    assert top["provider"] == "OpenAI"
    assert top["released_at"] == "2026-09-03"
    assert top["metric_count"] == 20
    assert top["confidence"] == "HIGH"
    assert top["score"] == 93.7
    assert top["cache_input_price_per_million_cny"] == 6.71
    assert top["input_price_per_million_cny"] == 67.08
    assert top["output_price_per_million_cny"] == 335.41
    # 上游对没有缓存价的模型给的是 “—”，解析成 None 由前端显示占位
    miss = next(item for item in items if item["slug"] == "muse-spark-1-3")
    assert miss["input_price_per_million_cny"] == 8.39
    assert miss["cache_input_price_per_million_cny"] is None
    # 新版载荷不再带上下文/输出上限，交给 models.dev 目录补齐
    assert top["context_window_tokens"] is None
    assert top["components"] == {}

    # 第 2 名开始的单元格在载荷里是 $L 分块下发的，引用必须回填成功
    second = items[1]
    assert second["slug"] == "claude-fable-5-1"
    assert second["name"] == "Claude Fable 5.1"
    assert second["rank"] == 2
    assert second["score"] == 93.7
    assert all(item["score"] is not None for item in items)


def test_parse_leaderboard_payload_rejects_unknown_shape() -> None:
    with pytest.raises(LeaderboardError, match="entries"):
        parse_leaderboard_payload("<!DOCTYPE html><html><body>维护中</body></html>")


def test_flight_payload_detects_structure_drift() -> None:
    # 上游改版换了 className 时，必须整体报错，不能把只有 slug 的空壳写进缓存
    broken = RSC_PAYLOAD.replace("lb-name-cell", "lb-renamed-name").replace("lb-score-cell", "lb-renamed-score")
    with pytest.raises(LeaderboardError, match="entries"):
        parse_leaderboard_payload(broken)


def test_canonical_model_key_strips_dates_and_qualifiers() -> None:
    assert canonical_model_key("anthropic/claude-fable-5-20260609") == "claude-fable-5"
    assert canonical_model_key("Claude Fable 5 Latest") == "claude-fable-5"


def test_mask_public_label_keeps_edges() -> None:
    assert mask_public_label("") == ""
    assert mask_public_label("A") == "*"
    assert mask_public_label("AB") == "A*"
    assert mask_public_label("ABC") == "A*C"
    assert mask_public_label("Claude Direct") == "Cl*********ct"
    assert mask_public_label("macbook-studio") == "ma**********io"


def test_leaderboard_requires_auth(client: TestClient) -> None:
    response = client.get("/api/admin/leaderboard")
    assert response.status_code == 401


def test_leaderboard_fills_output_window_from_catalog(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    from app.services.model_caps import CatalogIndex, ModelCaps

    index = CatalogIndex()
    caps = ModelCaps(
        context_window=200000,
        max_output_tokens=128000,
        reasoning=True,
        source="catalog",
    )
    index.by_provider_norm[("anthropic", "claude-fable-5")] = caps
    index.by_norm["claude-fable-5"] = caps
    monkeypatch.setattr("app.services.model_caps.load_catalog_index", lambda force=False: index)
    _seed_leaderboard(client, auth_headers)
    response = client.get("/api/admin/leaderboard", headers=auth_headers)
    assert response.status_code == 200
    # 新版载荷不带上下文/输出上限，榜单补齐要靠 models.dev 目录
    item = _find_item(response, "claude-fable-5")
    assert item["context_window_tokens"] == 200000
    assert item["max_output_tokens"] == 128000


def test_dashboard_includes_leaderboard_top(client: TestClient, auth_headers: dict[str, str]) -> None:
    _seed_leaderboard(client, auth_headers)
    dashboard = client.get("/api/admin/dashboard", headers=auth_headers)
    assert dashboard.status_code == 200
    top = dashboard.json()["leaderboard_top"]
    assert len(top) == 3
    assert top[0]["rank"] == 1
    assert top[0]["name"] == "GPT-6 Astra"
    assert top[0]["provider"] == "OpenAI"
    assert top[0]["score"] == 93.7
    assert top[0]["slug"] == "gpt-6-astra"
    assert top[0]["context_window_tokens"] is None
    assert top[0]["max_output_tokens"] is None


def test_admin_leaderboard_reads_cache_without_fetching(client: TestClient, auth_headers: dict[str, str]) -> None:
    with patch("app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=RSC_PAYLOAD)) as fetch:
        empty = client.get("/api/admin/leaderboard", headers=auth_headers)
        assert empty.status_code == 200, empty.text
        assert empty.json()["items"] == []
        assert fetch.await_count == 0
        seeded = client.post("/api/admin/jobs/leaderboard/run", headers=auth_headers)
        assert seeded.status_code == 200, seeded.text
        assert fetch.await_count == 1
        cached = client.get("/api/admin/leaderboard", headers=auth_headers)
        assert cached.status_code == 200
        body = cached.json()
        assert body["unofficial"] is True
        assert body["source_page"] == "https://aihot.news/leaderboard"
        assert body["total"] == 30
        assert body["stale"] is False
        item = _find_item(cached, "claude-fable-5")
        assert item["name"] == "Claude Fable 5"
        assert item["released_at"] == "2026-06-09"
        assert item["score"] == 93.3
        assert item["cache_input_price_per_million_cny"] == 6.71
        assert item["input_price_per_million_cny"] == 67.08
        assert item["output_price_per_million_cny"] == 335.41
        assert item["metric_count"] == 20
        assert item["confidence"] == "MEDIUM"
        assert item["context_window_tokens"] is None
        assert item["max_output_tokens"] is None
        assert item["local_covered"] is False
        assert item["local_matches"] == []
        assert fetch.await_count == 1


def test_admin_leaderboard_ignores_refresh_query(client: TestClient, auth_headers: dict[str, str]) -> None:
    with patch("app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=RSC_PAYLOAD)) as fetch:
        response = client.get("/api/admin/leaderboard?refresh=true", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert fetch.await_count == 0


def test_admin_leaderboard_returns_stale_cache(client: TestClient, auth_headers: dict[str, str]) -> None:
    session = get_session_factory()()
    try:
        session.add(
            LeaderboardSnapshot(
                source_url="https://aihot.news/leaderboard",
                fetched_at=utcnow() - timedelta(days=2),
                entries_json='[{"rank":1,"slug":"old-model","name":"Old Model","provider":"X","components":{}}]',
            )
        )
        session.commit()
    finally:
        session.close()

    with patch("app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=RSC_PAYLOAD)) as fetch:
        response = client.get("/api/admin/leaderboard", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["stale"] is True
    assert body["items"][0]["slug"] == "old-model"
    assert fetch.await_count == 0


def test_admin_leaderboard_empty_without_cache(client: TestClient, auth_headers: dict[str, str]) -> None:
    with patch("app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=RSC_PAYLOAD)) as fetch:
        response = client.get("/api/admin/leaderboard", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert fetch.await_count == 0


def _seed_leaderboard(client: TestClient, auth_headers: dict[str, str]) -> None:
    with patch("app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=RSC_PAYLOAD)):
        seeded = client.post("/api/admin/jobs/leaderboard/run", headers=auth_headers)
        assert seeded.status_code == 200, seeded.text


def _age_snapshot(days: int) -> None:
    """把缓存时间往前挪，模拟“任务在跑但一直拉取失败”的过期缓存。"""
    session = get_session_factory()()
    try:
        snapshot = session.scalar(
            select(LeaderboardSnapshot).order_by(LeaderboardSnapshot.id.desc()).limit(1)
        )
        assert snapshot is not None
        snapshot.fetched_at = utcnow() - timedelta(days=days)
        session.commit()
    finally:
        session.close()


def _seed_local_coverage(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Claude Direct", "provider": "anthropic_generic", "api_key": "sk-up"},
    )
    assert created.status_code == 200
    session = get_session_factory()()
    try:
        account = session.get(UpstreamAccount, created.json()["id"])
        assert account is not None
        account.models_json = '["anthropic/claude-fable-5-20260609", "anthropic/claude-fable-5-20260609"]'
        session.commit()
    finally:
        session.close()

    _sync_agent(
        "macbook-studio",
        {"claude-local": {"id": "claude-local", "name": "Claude Agent", "provider": "anthropic_generic"}},
    )
    session = get_session_factory()()
    try:
        agent_account = session.scalar(
            select(UpstreamAccount).where(UpstreamAccount.agent_route_id == "claude-local")
        )
        assert agent_account is not None
        agent_account.models_json = '["claude-fable-5"]'
        session.commit()
    finally:
        session.close()


def test_leaderboard_local_coverage_lists_accounts_and_agents(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _seed_local_coverage(client, auth_headers)
    _seed_leaderboard(client, auth_headers)
    response = client.get("/api/admin/leaderboard", headers=auth_headers)
    assert response.status_code == 200
    item = _find_item(response, "claude-fable-5")
    assert item["local_covered"] is True
    names = {match["account_name"] for match in item["local_matches"]}
    kinds = {match["kind"] for match in item["local_matches"]}
    matched_models = {match["matched_model"] for match in item["local_matches"]}
    assert names == {"Claude Direct", "Claude Agent"}
    assert kinds == {"account", "agent"}
    assert len(item["local_matches"]) == 2
    assert "anthropic/claude-fable-5-20260609" in matched_models
    assert "claude-fable-5" in matched_models
    agent_match = next(match for match in item["local_matches"] if match["kind"] == "agent")
    assert agent_match["agent_id"] == "macbook-studio"
    assert agent_match["agent_route_id"] == "claude-local"


def test_public_leaderboard_masks_account_info(client: TestClient, auth_headers: dict[str, str]) -> None:
    _seed_local_coverage(client, auth_headers)
    _seed_leaderboard(client, auth_headers)
    response = client.get("/api/share/leaderboard")
    assert response.status_code == 200
    item = _find_item(response, "claude-fable-5")
    assert item["local_covered"] is True
    names = {match["account_name"] for match in item["local_matches"]}
    assert names == {"Cl*********ct", "Cl********nt"}
    assert all(match["account_id"] == 0 for match in item["local_matches"])
    agent_match = next(match for match in item["local_matches"] if match["kind"] == "agent")
    assert agent_match["agent_id"] == "ma**********io"
    assert agent_match["agent_route_id"] == "cl********al"
    assert "Claude Direct" not in response.text
    assert "macbook-studio" not in response.text


def test_public_leaderboard_does_not_fetch(client: TestClient, auth_headers: dict[str, str]) -> None:
    with patch("app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=RSC_PAYLOAD)) as fetch:
        empty = client.get("/api/share/leaderboard")
        assert empty.status_code == 200
        assert empty.json()["items"] == []
        admin = client.get("/api/admin/leaderboard", headers=auth_headers)
        assert admin.status_code == 200
        assert admin.json()["items"] == []
        assert fetch.await_count == 0
        created = client.post("/api/admin/jobs/leaderboard/run", headers=auth_headers)
        assert created.status_code == 200
        public = client.get("/api/share/leaderboard?refresh=true")
        assert public.status_code == 200
        assert _find_item(public, "claude-fable-5")["score"] == 93.3
        assert fetch.await_count == 1


def _seed_benchmark(client: TestClient, auth_headers: dict[str, str], *, model: str, ok: bool, speed: float = 50.0) -> None:
    saved = client.post(
        "/api/admin/benchmark/history",
        headers=auth_headers,
        json={
            "prompt": "测速",
            "max_tokens": 32,
            "results": [
                {
                    "account_id": 1,
                    "account_name": "Bench Acct",
                    "provider": "anthropic",
                    "model": model,
                    "ok": ok,
                    "timeout": False,
                    "first_token_ms": 120 if ok else None,
                    "total_ms": 800 if ok else None,
                    "output_chars": 16 if ok else None,
                    "estimated_output_tokens": 40 if ok else None,
                    "output_tokens_per_second": speed,
                    "preview": "ok" if ok else None,
                    "error": None if ok else "boom",
                }
            ],
        },
    )
    assert saved.status_code == 200, saved.text


def test_leaderboard_attaches_latest_successful_benchmark(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _seed_benchmark(client, auth_headers, model="claude-fable-5", ok=True, speed=88.5)
    _seed_benchmark(client, auth_headers, model="claude-fable-5", ok=False, speed=999)
    _seed_leaderboard(client, auth_headers)
    response = client.get("/api/admin/leaderboard", headers=auth_headers)
    assert response.status_code == 200
    item = _find_item(response, "claude-fable-5")
    assert item["benchmark"] is not None
    assert item["benchmark"]["model"] == "claude-fable-5"
    assert item["benchmark"]["account_name"] == "Bench Acct"
    assert item["benchmark"]["output_tokens_per_second"] == 88.5
    assert item["benchmark"]["first_token_ms"] == 120.0
    assert item["benchmark"]["created_at"] is not None


def test_leaderboard_benchmark_is_none_without_results(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _seed_leaderboard(client, auth_headers)
    response = client.get("/api/admin/leaderboard", headers=auth_headers)
    assert response.status_code == 200
    item = _find_item(response, "claude-fable-5")
    assert item["benchmark"] is None


def test_public_leaderboard_masks_benchmark_account(client: TestClient, auth_headers: dict[str, str]) -> None:
    _seed_benchmark(client, auth_headers, model="claude-fable-5", ok=True, speed=77.0)
    _seed_leaderboard(client, auth_headers)
    response = client.get("/api/share/leaderboard")
    assert response.status_code == 200
    item = _find_item(response, "claude-fable-5")
    assert item["benchmark"] is not None
    assert item["benchmark"]["account_name"] == "Be******ct"
    assert "Bench Acct" not in response.text


def test_leaderboard_refresh_failure_keeps_cache_and_reports_error(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _seed_leaderboard(client, auth_headers)
    _age_snapshot(days=2)
    reason = "榜单载荷中没有 entries（AIHOT 页面结构可能已改版）"

    with patch(
        "app.services.leaderboard.fetch_leaderboard_text",
        new=AsyncMock(side_effect=LeaderboardError(reason)),
    ):
        failed = client.post("/api/admin/jobs/leaderboard/run", headers=auth_headers)
    assert failed.status_code == 502
    assert reason in failed.json()["detail"]

    # 刷新失败不能只在 message 里写一句，任务面板要显示失败
    jobs = client.get("/api/admin/jobs", headers=auth_headers).json()["items"]
    job = next(item for item in jobs if item["id"] == "leaderboard")
    assert job["last_ok"] is False
    assert job["error_message"] is not None
    assert "沿用缓存 30 条榜单" in job["error_message"]

    # 榜单页面继续用旧缓存，但要标出 stale 与失败原因
    cached = client.get("/api/admin/leaderboard", headers=auth_headers)
    assert cached.status_code == 200
    body = cached.json()
    assert body["total"] == 30
    assert body["stale"] is True
    assert reason in body["error_message"]

    # 公开页只给通用提示，不下发内部细节
    public = client.get("/api/share/leaderboard")
    assert public.status_code == 200
    assert public.json()["total"] == 30
    assert public.json()["stale"] is True
    assert public.json()["error_message"] == "榜单同步失败，当前展示最近一次缓存"

    # 恢复成功后错误信息要清空
    with patch("app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=RSC_PAYLOAD)):
        recovered = client.post("/api/admin/jobs/leaderboard/run", headers=auth_headers)
    assert recovered.status_code == 200, recovered.text
    recovered_job = next(item for item in recovered.json()["items"] if item["id"] == "leaderboard")
    assert recovered_job["last_ok"] is True
    assert recovered_job["error_message"] is None
    recovered_cache = client.get("/api/admin/leaderboard", headers=auth_headers).json()
    assert recovered_cache["error_message"] is None
    assert recovered_cache["stale"] is False


def test_leaderboard_refresh_failure_without_cache(client: TestClient, auth_headers: dict[str, str]) -> None:
    with patch(
        "app.services.leaderboard.fetch_leaderboard_text",
        new=AsyncMock(side_effect=LeaderboardError("拉取榜单失败：连接超时")),
    ):
        response = client.post("/api/admin/jobs/leaderboard/run", headers=auth_headers)
    assert response.status_code == 502
    assert "拉取榜单失败" in response.json()["detail"]
    jobs = client.get("/api/admin/jobs", headers=auth_headers).json()["items"]
    job = next(item for item in jobs if item["id"] == "leaderboard")
    assert job["last_ok"] is False
    assert job["error_message"] == "拉取榜单失败：连接超时"

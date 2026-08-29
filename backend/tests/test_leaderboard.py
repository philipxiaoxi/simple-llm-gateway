from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.clock import utcnow
from app.db import get_session_factory
from app.models import LeaderboardSnapshot, UpstreamAccount
from app.routers.local_agent import _sync_agent
from app.services.leaderboard import canonical_model_key, mask_public_label, parse_leaderboard_payload


RSC_PAYLOAD = (
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


def test_parse_leaderboard_payload() -> None:
    items = parse_leaderboard_payload(RSC_PAYLOAD)
    assert len(items) == 1
    assert items[0]["slug"] == "claude-fable-5"
    assert items[0]["score"] == 89.2
    assert items[0]["released_at"] == "2026-06-09T00:00:00.000Z"
    assert items[0]["summary"] == "by 6 families"
    assert items[0]["context_window_tokens"] == 1000000
    assert items[0]["components"]["artificial-analysis"]["coverage"] == 0.3


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
    index.by_norm["claude-fable-5"] = ModelCaps(
        context_window=200000,
        max_output_tokens=128000,
        reasoning=True,
        source="catalog",
    )
    monkeypatch.setattr("app.services.model_caps.load_catalog_index", lambda force=False: index)
    with patch("app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=RSC_PAYLOAD)):
        response = client.get("/api/admin/leaderboard", headers=auth_headers)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["context_window_tokens"] == 1000000
    assert item["max_output_tokens"] == 128000


def test_dashboard_includes_leaderboard_top(client: TestClient, auth_headers: dict[str, str]) -> None:
    with patch("app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=RSC_PAYLOAD)):
        seeded = client.get("/api/admin/leaderboard", headers=auth_headers)
        assert seeded.status_code == 200
    dashboard = client.get("/api/admin/dashboard", headers=auth_headers)
    assert dashboard.status_code == 200
    top = dashboard.json()["leaderboard_top"]
    assert len(top) == 1
    assert top[0]["rank"] == 1
    assert top[0]["name"] == "Claude Fable 5"
    assert top[0]["provider"] == "Anthropic"
    assert top[0]["score"] == 89.2
    assert top[0]["slug"] == "claude-fable-5"
    assert top[0]["context_window_tokens"] == 1000000
    assert top[0]["max_output_tokens"] is None


def test_leaderboard_fetches_and_caches(client: TestClient, auth_headers: dict[str, str]) -> None:
    with patch("app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=RSC_PAYLOAD)) as fetch:
        first = client.get("/api/admin/leaderboard", headers=auth_headers)
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["unofficial"] is True
        assert body["source_page"] == "https://aihot.virxact.com/leaderboard"
        assert body["total"] == 1
        assert body["items"][0]["name"] == "Claude Fable 5"
        assert body["items"][0]["released_at"] == "2026-06-09T00:00:00.000Z"
        assert body["items"][0]["summary"] == "by 6 families"
        assert body["items"][0]["context_window_tokens"] == 1000000
        assert body["items"][0]["max_output_tokens"] is None
        assert body["items"][0]["local_covered"] is False
        assert body["items"][0]["local_matches"] == []
        assert body["stale"] is False
        second = client.get("/api/admin/leaderboard", headers=auth_headers)
        assert second.status_code == 200
        assert second.json()["items"][0]["slug"] == "claude-fable-5"
        assert fetch.await_count == 1


def test_leaderboard_force_refresh_respects_min_interval(client: TestClient, auth_headers: dict[str, str]) -> None:
    with patch("app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=RSC_PAYLOAD)) as fetch:
        created = client.get("/api/admin/leaderboard", headers=auth_headers)
        assert created.status_code == 200
        refreshed = client.get("/api/admin/leaderboard?refresh=true", headers=auth_headers)
        assert refreshed.status_code == 200
        assert "刷新过于频繁" in (refreshed.json()["error_message"] or "")
        assert fetch.await_count == 1


def test_leaderboard_returns_stale_cache_on_fetch_error(client: TestClient, auth_headers: dict[str, str]) -> None:
    session = get_session_factory()()
    try:
        session.add(
            LeaderboardSnapshot(
                source_url="https://aihot.virxact.com/leaderboard",
                fetched_at=utcnow() - timedelta(days=2),
                entries_json='[{"rank":1,"slug":"old-model","name":"Old Model","provider":"X","components":{}}]',
            )
        )
        session.commit()
    finally:
        session.close()

    from app.services.leaderboard import LeaderboardError

    with patch(
        "app.services.leaderboard.fetch_leaderboard_text",
        new=AsyncMock(side_effect=LeaderboardError("拉取榜单失败")),
    ):
        response = client.get("/api/admin/leaderboard", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["stale"] is True
    assert body["items"][0]["slug"] == "old-model"
    assert body["error_message"] == "拉取榜单失败"


def test_leaderboard_fails_without_cache(client: TestClient, auth_headers: dict[str, str]) -> None:
    from app.services.leaderboard import LeaderboardError

    with patch(
        "app.services.leaderboard.fetch_leaderboard_text",
        new=AsyncMock(side_effect=LeaderboardError("拉取榜单失败")),
    ):
        response = client.get("/api/admin/leaderboard", headers=auth_headers)
    assert response.status_code == 502
    assert response.json()["detail"] == "拉取榜单失败"


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
    with patch("app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=RSC_PAYLOAD)):
        response = client.get("/api/admin/leaderboard", headers=auth_headers)
    assert response.status_code == 200
    item = response.json()["items"][0]
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
    with patch("app.services.leaderboard.fetch_leaderboard_text", new=AsyncMock(return_value=RSC_PAYLOAD)):
        seeded = client.get("/api/admin/leaderboard", headers=auth_headers)
        assert seeded.status_code == 200
        response = client.get("/api/share/leaderboard")
    assert response.status_code == 200
    item = response.json()["items"][0]
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
        assert fetch.await_count == 0
        created = client.get("/api/admin/leaderboard", headers=auth_headers)
        assert created.status_code == 200
        public = client.get("/api/share/leaderboard?refresh=true")
        assert public.status_code == 200
        assert public.json()["items"][0]["slug"] == "claude-fable-5"
        assert fetch.await_count == 1

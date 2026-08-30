from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.models import GatewayAgent, GatewayAgentRoute, LeaderboardSnapshot, UpstreamAccount
from app.services import model_caps as model_caps_service

USER_AGENT = "simple-llm-gateway/0.1 (internal cache; not a public mirror)"
RSC_HEADERS = {
    "RSC": "1",
    "Accept": "text/x-component",
    "User-Agent": USER_AGENT,
}


class LeaderboardError(RuntimeError):
    pass


def _extract_json_array(text: str, marker: str) -> list[Any]:
    start = text.find(marker)
    if start < 0:
        raise LeaderboardError("榜单载荷中没有 entries")
    i = text.find("[", start)
    if i < 0:
        raise LeaderboardError("榜单载荷格式无效")
    depth = 0
    in_str = False
    esc = False
    end = None
    for j, ch in enumerate(text[i:], i):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    if end is None:
        raise LeaderboardError("榜单载荷未闭合")
    try:
        data = json.loads(text[i:end])
    except json.JSONDecodeError as error:
        raise LeaderboardError("榜单 JSON 解析失败") from error
    if not isinstance(data, list):
        raise LeaderboardError("榜单 entries 不是数组")
    return data


def parse_leaderboard_payload(text: str) -> list[dict[str, Any]]:
    raw_entries = _extract_json_array(text, '"entries":[')
    entries: list[dict[str, Any]] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        name = str(item.get("name") or "").strip()
        if not slug or not name:
            continue
        components: dict[str, dict[str, Any]] = {}
        raw_components = item.get("components") or {}
        if isinstance(raw_components, dict):
            for key, value in raw_components.items():
                if not isinstance(value, dict):
                    continue
                components[str(key)] = {
                    "score": value.get("score"),
                    "coverage": value.get("coverage"),
                    "metric_count": value.get("metricCount"),
                }
        entries.append(
            {
                "rank": item.get("rank"),
                "previous_rank": item.get("previousRank"),
                "rank_change": item.get("rankChange"),
                "slug": slug,
                "name": name,
                "provider": str(item.get("provider") or "").strip(),
                "provider_slug": item.get("providerSlug"),
                "released_at": item.get("releasedAt"),
                "context_window_tokens": item.get("contextWindowTokens"),
                "pricing_kind": item.get("pricingKind"),
                "pricing_official_model_id": item.get("pricingOfficialModelId"),
                "input_price_per_million_usd": item.get("inputPricePerMillionUsd"),
                "output_price_per_million_usd": item.get("outputPricePerMillionUsd"),
                "input_price_per_million_cny": item.get("inputPricePerMillionCny"),
                "output_price_per_million_cny": item.get("outputPricePerMillionCny"),
                "price_quote": item.get("priceQuote"),
                "pricing_source_name": item.get("pricingSourceName"),
                "pricing_source_url": item.get("pricingSourceUrl"),
                "score": item.get("score"),
                "uncertainty": item.get("uncertainty"),
                "coverage": item.get("coverage"),
                "confidence": item.get("confidence"),
                "possible_rank_from": item.get("possibleRankFrom"),
                "possible_rank_to": item.get("possibleRankTo"),
                "metric_count": item.get("metricCount"),
                "summary": item.get("summary"),
                "components": components,
            }
        )
    if not entries:
        raise LeaderboardError("榜单没有可用条目")
    return entries


_DATE_SUFFIX = re.compile(r"-\d{8}$")
_QUALIFIER_SUFFIXES = ("-latest", "-preview", "-exp")


def normalize_model_key(value: str | None) -> str:
    text = str(value or "").strip().lower().replace("_", "-").replace(" ", "-").replace(".", "-")
    while "--" in text:
        text = text.replace("--", "-")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.strip("-")


def canonical_model_key(value: str | None) -> str:
    text = normalize_model_key(value)
    text = _DATE_SUFFIX.sub("", text)
    for suffix in _QUALIFIER_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.strip("-")


def entry_match_keys(entry: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for raw in (entry.get("slug"), entry.get("pricing_official_model_id"), entry.get("name")):
        key = canonical_model_key(raw if raw is None else str(raw))
        if key:
            keys.add(key)
    return keys


def model_ids_match(local_id: str, entry_keys: set[str]) -> bool:
    local = canonical_model_key(local_id)
    return bool(local) and local in entry_keys


def _local_model_sources(db: Session) -> list[dict[str, Any]]:
    routes = {route.route_id: route for route in db.scalars(select(GatewayAgentRoute)).all()}
    agents = {agent.id: agent for agent in db.scalars(select(GatewayAgent)).all()}
    sources: list[dict[str, Any]] = []
    accounts = db.scalars(select(UpstreamAccount).order_by(UpstreamAccount.id.asc())).all()
    for account in accounts:
        models = model_caps_service.parse_models_json(account.models_json)
        agent_id = None
        if account.source == "agent" and account.agent_route_id:
            route = routes.get(account.agent_route_id)
            if route is not None:
                agent = agents.get(route.agent_id)
                agent_id = agent.agent_id if agent is not None else None
                for model in model_caps_service.parse_models_json(route.models_json):
                    if model not in models:
                        models.append(model)
        sources.append(
            {
                "kind": "agent" if account.source == "agent" else "account",
                "account_id": account.id,
                "account_name": account.name,
                "provider": account.provider,
                "agent_id": agent_id,
                "agent_route_id": account.agent_route_id,
                "models": models,
            }
        )
    return sources


def attach_local_coverage(db: Session, items: list[dict[str, Any]]) -> None:
    sources = _local_model_sources(db)
    for item in items:
        keys = entry_match_keys(item)
        matches: list[dict[str, Any]] = []
        seen: set[tuple[str, int, str]] = set()
        for source in sources:
            for model in source["models"]:
                model_id = str(model)
                if not model_ids_match(model_id, keys):
                    continue
                fingerprint = (str(source["kind"]), int(source["account_id"]), model_id)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                matches.append(
                    {
                        "kind": source["kind"],
                        "account_id": source["account_id"],
                        "account_name": source["account_name"],
                        "provider": source["provider"],
                        "agent_id": source["agent_id"],
                        "agent_route_id": source["agent_route_id"],
                        "matched_model": model_id,
                    }
                )
        item["local_covered"] = bool(matches)
        item["local_matches"] = matches


_LEADERBOARD_CATALOG_PROVIDERS = {
    "anthropic": "anthropic",
    "openai": "openai",
    "xai": "xai",
    "deepseek": "deepseek",
    "google": "google",
    "gemini": "google",
    "moonshot": "moonshotai",
    "moonshot ai": "moonshotai",
    "alibaba": "alibaba",
    "qwen": "alibaba",
    "qianwen": "alibaba",
    "z.ai": "zai",
    "zai": "zai",
    "meta": "meta",
}


def _leaderboard_catalog_provider(item: dict[str, Any]) -> str | None:
    for raw in (item.get("provider_slug"), item.get("provider")):
        key = str(raw or "").strip().lower()
        if key in _LEADERBOARD_CATALOG_PROVIDERS:
            return _LEADERBOARD_CATALOG_PROVIDERS[key]
    return None


def _catalog_caps_for_entry(item: dict[str, Any], catalog):
    catalog_provider = _leaderboard_catalog_provider(item)
    candidates: list[str] = []
    for raw in (item.get("pricing_official_model_id"), item.get("slug"), item.get("name")):
        if not raw:
            continue
        text = str(raw)
        candidates.append(text)
        key = canonical_model_key(text)
        if key:
            candidates.append(key)
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        matched = model_caps_service.match_catalog(
            candidate,
            None,
            catalog,
            catalog_provider=catalog_provider,
            fallback=False,
        )
        if matched is not None:
            return matched
    if catalog_provider is not None:
        return None
    for candidate in candidates:
        matched = model_caps_service.match_catalog(candidate, None, catalog, fallback=True)
        if matched is not None:
            return matched
    return None


def attach_catalog_windows(items: list[dict[str, Any]]) -> None:
    catalog = model_caps_service.load_catalog_index()
    for item in items:
        caps = _catalog_caps_for_entry(item, catalog)
        if caps is None:
            item.setdefault("max_output_tokens", None)
            continue
        if not item.get("context_window_tokens"):
            item["context_window_tokens"] = caps.context_window
        item["max_output_tokens"] = caps.max_output_tokens


def mask_public_label(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    length = len(text)
    if length == 1:
        return "*"
    if length == 2:
        return f"{text[0]}*"
    if length <= 6:
        return f"{text[0]}{'*' * (length - 2)}{text[-1]}"
    return f"{text[:2]}{'*' * (length - 4)}{text[-2:]}"


def mask_local_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    masked: list[dict[str, Any]] = []
    for match in matches:
        agent_id = mask_public_label(match.get("agent_id"))
        agent_route_id = mask_public_label(match.get("agent_route_id"))
        masked.append(
            {
                "kind": match.get("kind") or "account",
                "account_id": 0,
                "account_name": mask_public_label(match.get("account_name")),
                "provider": str(match.get("provider") or ""),
                "agent_id": agent_id or None,
                "agent_route_id": agent_route_id or None,
                "matched_model": match.get("matched_model") or "",
            }
        )
    return masked


def _latest_snapshot(db: Session) -> LeaderboardSnapshot | None:
    return db.scalar(select(LeaderboardSnapshot).order_by(LeaderboardSnapshot.id.desc()).limit(1))


def latest_snapshot(db: Session) -> LeaderboardSnapshot | None:
    return _latest_snapshot(db)


def snapshot_to_payload(
    db: Session,
    snapshot: LeaderboardSnapshot | None,
    *,
    stale: bool = False,
    error_message: str | None = None,
    public: bool = False,
) -> dict[str, Any]:
    from app.services.job_settings import get_job_int

    settings = get_settings()
    items: list[dict[str, Any]] = []
    if snapshot and snapshot.entries_json:
        try:
            loaded = json.loads(snapshot.entries_json)
            if isinstance(loaded, list):
                items = [item for item in loaded if isinstance(item, dict)]
        except json.JSONDecodeError:
            items = []
    attach_local_coverage(db, items)
    attach_catalog_windows(items)
    if public:
        for item in items:
            item["local_matches"] = mask_local_matches(item.get("local_matches") or [])
    return {
        "source_url": settings.aihot_leaderboard_url,
        "source_page": settings.aihot_leaderboard_url,
        "fetched_at": snapshot.fetched_at if snapshot else None,
        "stale": stale,
        "ttl_seconds": max(60, get_job_int("leaderboard", "interval_seconds", settings.aihot_leaderboard_ttl_seconds)),
        "min_refresh_seconds": max(0, settings.aihot_leaderboard_min_refresh_seconds),
        "source_updated_label": snapshot.source_updated_label if snapshot else None,
        "error_message": error_message or (snapshot.error_message if snapshot else None),
        "unofficial": True,
        "items": items,
        "total": len(items),
    }


def cache_is_fresh(snapshot: LeaderboardSnapshot | None, now: datetime | None = None) -> bool:
    if snapshot is None or not snapshot.entries_json or snapshot.entries_json == "[]":
        return False
    from app.services.job_settings import get_job_int

    ttl = max(60, get_job_int("leaderboard", "interval_seconds", get_settings().aihot_leaderboard_ttl_seconds))
    return snapshot.fetched_at >= (now or utcnow()) - timedelta(seconds=ttl)


def refresh_is_too_soon(snapshot: LeaderboardSnapshot | None, now: datetime | None = None) -> bool:
    if snapshot is None:
        return False
    min_refresh = max(0, get_settings().aihot_leaderboard_min_refresh_seconds)
    return snapshot.fetched_at >= (now or utcnow()) - timedelta(seconds=min_refresh)


async def fetch_leaderboard_text(client: httpx.AsyncClient | None = None) -> str:
    settings = get_settings()
    timeout = min(30, max(5, settings.request_timeout_seconds))
    try:
        if client is not None:
            response = await client.get(
                settings.aihot_leaderboard_url,
                headers=RSC_HEADERS,
                timeout=timeout,
                follow_redirects=True,
            )
        else:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http_client:
                response = await http_client.get(settings.aihot_leaderboard_url, headers=RSC_HEADERS)
        response.raise_for_status()
        return response.text
    except httpx.HTTPError as error:
        raise LeaderboardError(f"拉取榜单失败：{error}") from error


def save_snapshot(
    db: Session,
    entries: list[dict[str, Any]],
    *,
    error_message: str | None = None,
    source_updated_label: str | None = None,
) -> LeaderboardSnapshot:
    snapshot = _latest_snapshot(db)
    if snapshot is None:
        snapshot = LeaderboardSnapshot(source_url=get_settings().aihot_leaderboard_url)
        db.add(snapshot)
    snapshot.source_url = get_settings().aihot_leaderboard_url
    snapshot.fetched_at = utcnow()
    snapshot.entries_json = json.dumps(entries, ensure_ascii=False)
    snapshot.source_updated_label = source_updated_label
    snapshot.error_message = error_message
    db.commit()
    db.refresh(snapshot)
    return snapshot


async def get_leaderboard(
    db: Session,
    *,
    force: bool = False,
    public: bool = False,
    ignore_cooldown: bool = False,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    snapshot = _latest_snapshot(db)
    if public or not force:
        has_entries = bool(snapshot and snapshot.entries_json and snapshot.entries_json != "[]")
        return snapshot_to_payload(
            db,
            snapshot,
            stale=has_entries and not cache_is_fresh(snapshot),
            public=public,
        )
    if force and not ignore_cooldown and refresh_is_too_soon(snapshot):
        return snapshot_to_payload(
            db, snapshot, stale=False, error_message="刷新过于频繁，已返回缓存"
        )
    try:
        text = await fetch_leaderboard_text(client)
        entries = parse_leaderboard_payload(text)
        snapshot = save_snapshot(db, entries)
        return snapshot_to_payload(db, snapshot)
    except LeaderboardError as error:
        if snapshot and snapshot.entries_json and snapshot.entries_json != "[]":
            return snapshot_to_payload(db, snapshot, stale=True, error_message=str(error))
        raise

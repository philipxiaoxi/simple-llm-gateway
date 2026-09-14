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
from app.models import BenchmarkResult, BenchmarkRun, GatewayAgent, GatewayAgentRoute, LeaderboardSnapshot, UpstreamAccount
from app.services import model_caps as model_caps_service

USER_AGENT = "simple-llm-gateway/0.1 (internal cache; not a public mirror)"
# 排行榜/仪表盘只回看最近这么多次测速，避免随历史增长全表重算
BENCHMARK_LOOKBACK_RUNS = 30
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


def _empty_entry() -> dict[str, Any]:
    return {
        "rank": None,
        "previous_rank": None,
        "rank_change": None,
        "slug": "",
        "name": "",
        "provider": "",
        "provider_slug": None,
        "released_at": None,
        "context_window_tokens": None,
        "pricing_kind": None,
        "pricing_official_model_id": None,
        "input_price_per_million_usd": None,
        "output_price_per_million_usd": None,
        "cache_input_price_per_million_cny": None,
        "input_price_per_million_cny": None,
        "output_price_per_million_cny": None,
        "price_quote": None,
        "pricing_source_name": None,
        "pricing_source_url": None,
        "score": None,
        "uncertainty": None,
        "coverage": None,
        "confidence": None,
        "possible_rank_from": None,
        "possible_rank_to": None,
        "metric_count": None,
        "summary": None,
        "components": {},
    }


def _normalize_entries(raw_entries: list[Any]) -> list[dict[str, Any]]:
    """旧版载荷：AIHOT 直接内联 {"entries":[...]} 的 JSON。"""
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
        entry = _empty_entry()
        entry.update(
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
        entries.append(entry)
    if not entries:
        raise LeaderboardError("榜单没有可用条目")
    return entries


# ---- 新版载荷：React Server Component 飞行数据 ----
# AIHOT 改版后不再内联 {"entries":[...]}，而是把榜单表格直接渲染进 RSC 载荷
# （Content-Type: text/x-component）。表格行形如
#   ["$","tr","claude-fable-5",{"children":[["$","td",null,{"className":"lb-rank-number",...
# 前两行内联在页面数据块里，其余行以 $L<数据块 id> 的形式流式分块下发。
# 解析步骤：先按行收集单行 JSON 数据块 → 回填 $L 引用 → 按 className 抽取字段。
_FLIGHT_JSON_ROW = re.compile(r"^([0-9a-f]+):(\[.*\])\s*$")
_FLIGHT_REFERENCE = re.compile(r"^\$(?:L)?([0-9a-f]+)$")
_FLIGHT_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_FLIGHT_TABLE_CLASS = "lb-ranking-table"


def _flight_chunks(text: str) -> dict[str, Any]:
    """收集飞行载荷里单行 JSON 数据块（表格行与单元格都在其中）。"""
    chunks: dict[str, Any] = {}
    for line in text.splitlines():
        match = _FLIGHT_JSON_ROW.match(line)
        if match is None:
            continue
        try:
            chunks[match.group(1)] = json.loads(match.group(2))
        except json.JSONDecodeError:
            continue
    return chunks


def _flight_resolve(value: Any, chunks: dict[str, Any], seen: frozenset[str] = frozenset()) -> Any:
    """把 $L<id> / $<id> 引用替换成对应数据块的内容。"""
    if isinstance(value, str):
        match = _FLIGHT_REFERENCE.match(value)
        if match is None:
            return value
        chunk_id = match.group(1)
        if chunk_id in seen or chunk_id not in chunks:
            return None
        return _flight_resolve(chunks[chunk_id], chunks, seen | {chunk_id})
    if isinstance(value, list):
        return [_flight_resolve(item, chunks, seen) for item in value]
    if isinstance(value, dict):
        return {key: _flight_resolve(item, chunks, seen) for key, item in value.items()}
    return value


def _flight_element(value: Any) -> list[Any] | None:
    """飞行元素形如 ["$", 标签, key, props]，其余（文本、数字、null）返回 None。"""
    if isinstance(value, list) and len(value) >= 4 and value[0] == "$" and isinstance(value[3], dict):
        return value
    return None


def _flight_children(value: Any) -> list[Any]:
    """children 可能是单个元素、元素数组或纯文本数组，统一拍平一层。"""
    if value is None:
        return []
    if isinstance(value, list):
        if value and value[0] == "$":
            return [value]
        nodes: list[Any] = []
        for item in value:
            nodes.extend(_flight_children(item))
        return nodes
    return [value]


def _flight_text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return "" if value == "$undefined" else value
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        element = _flight_element(value)
        if element is not None:
            return _flight_text(element[3].get("children"))
        return "".join(_flight_text(item) for item in value)
    return ""


def _flight_descendants(value: Any) -> list[list[Any]]:
    found: list[list[Any]] = []
    for node in _flight_children(value):
        element = _flight_element(node)
        if element is None:
            continue
        found.append(element)
        found.extend(_flight_descendants(element[3].get("children")))
    return found


def _flight_find_class(value: Any, needle: str) -> list[Any] | None:
    element = _flight_element(value)
    if element is not None:
        class_name = element[3].get("className")
        if isinstance(class_name, str) and needle in class_name:
            return element
        return _flight_find_class(element[3].get("children"), needle)
    if isinstance(value, list):
        for item in value:
            found = _flight_find_class(item, needle)
            if found is not None:
                return found
    return None


def _flight_first_tag(value: Any, tag: str) -> list[Any] | None:
    for element in _flight_descendants(value):
        if element[1] == tag:
            return element
    return None


def _flight_number(value: Any) -> float | None:
    match = _FLIGHT_NUMBER.search(re.sub(r"[,\s]", "", _flight_text(value)))
    return float(match.group(0)) if match else None


def _flight_score(props: dict[str, Any]) -> float | None:
    meter = _flight_first_tag(props.get("children"), "meter")
    if meter is not None:
        raw = meter[3].get("value")
        if isinstance(raw, int | float) and not isinstance(raw, bool):
            return float(raw)
        parsed = _flight_number(raw)
        if parsed is not None:
            return parsed
    strong = _flight_first_tag(props.get("children"), "strong")
    return _flight_number(strong if strong is not None else props.get("children"))


def _flight_entry(row: list[Any], chunks: dict[str, Any]) -> dict[str, Any] | None:
    entry = _empty_entry()
    slug = str(row[2] or "").strip()
    named = False
    prices: list[float | None] = []
    for node in _flight_children(row[3].get("children")):
        cell = _flight_element(_flight_resolve(node, chunks))
        if cell is None:
            continue
        props = cell[3]
        class_name = str(props.get("className") or "")
        if "lb-rank-number" in class_name:
            rank = _flight_number(props.get("children"))
            entry["rank"] = int(rank) if rank is not None else None
        elif "lb-name-cell" in class_name:
            if not slug:
                for element in _flight_descendants(props.get("children")):
                    href = element[3].get("href")
                    if isinstance(href, str) and href.startswith("/leaderboard/"):
                        slug = href.rsplit("/", 1)[-1].strip()
                        break
            entry["name"] = _flight_text(_flight_first_tag(props.get("children"), "strong")).strip()
            entry["provider"] = _flight_text(_flight_first_tag(props.get("children"), "small")).strip()
            named = bool(entry["name"])
        elif "lb-release-cell" in class_name:
            time_element = _flight_first_tag(props.get("children"), "time")
            released = str(time_element[3].get("dateTime") or "").strip() if time_element is not None else ""
            entry["released_at"] = released or _flight_text(props.get("children")).strip() or None
        elif "lb-evidence-cell" in class_name:
            metric = _flight_number(_flight_first_tag(props.get("children"), "span"))
            entry["metric_count"] = int(metric) if metric is not None else None
            confidence_element = _flight_first_tag(props.get("children"), "small")
            if confidence_element is not None:
                confidence = str(confidence_element[3].get("data-confidence") or "").strip()
                entry["confidence"] = confidence or None
        elif "lb-price-cell" in class_name:
            prices.append(_flight_number(props.get("children")))
        elif "lb-score-cell" in class_name:
            entry["score"] = _flight_score(props)
    # 缺 slug / 名字 / 分数的行说明页面结构与解析假设已经对不上，宁可整体报错也不要
    # 把空壳条目写进缓存（写进去之后页面看着“有数据”，实际全是 slug）
    if not slug or not named or entry["score"] is None:
        return None
    entry["slug"] = slug
    # 价格列固定为 缓存输入 / 输入 / 输出（人民币 / 百万 Token），新版没有再给美元价
    if len(prices) == 3:
        entry["cache_input_price_per_million_cny"] = prices[0]
        entry["input_price_per_million_cny"] = prices[1]
        entry["output_price_per_million_cny"] = prices[2]
    elif len(prices) == 2:
        entry["input_price_per_million_cny"] = prices[0]
        entry["output_price_per_million_cny"] = prices[1]
    return entry


def _flight_table_rows(table: list[Any], chunks: dict[str, Any]) -> list[list[Any]]:
    body: list[Any] | None = None
    for node in _flight_children(table[3].get("children")):
        element = _flight_element(node)
        if element is not None and element[1] == "tbody":
            body = element
            break
    source = _flight_children(body[3].get("children")) if body is not None else _flight_children(table[3].get("children"))
    rows: list[list[Any]] = []
    for node in source:
        row = _flight_element(_flight_resolve(node, chunks))
        if row is not None and row[1] == "tr":
            rows.append(row)
    return rows


def _parse_flight_entries(text: str) -> list[dict[str, Any]]:
    chunks = _flight_chunks(text)
    if not chunks:
        return []
    for chunk in chunks.values():
        table = _flight_find_class(chunk, _FLIGHT_TABLE_CLASS)
        if table is None:
            continue
        rows = _flight_table_rows(table, chunks)
        entries = [entry for entry in (_flight_entry(row, chunks) for row in rows) if entry]
        # 行数与解析结果对不上就认为结构变了，返回空让上层报错并保留旧缓存
        if rows and len(entries) == len(rows):
            return entries
        return []
    return []


def parse_leaderboard_payload(text: str) -> list[dict[str, Any]]:
    if '"entries":[' in text:
        try:
            return _normalize_entries(_extract_json_array(text, '"entries":['))
        except LeaderboardError:
            # 新版页面里也可能恰好出现同名字符串，继续按飞行载荷解析
            pass
    entries = _parse_flight_entries(text)
    if not entries:
        raise LeaderboardError("榜单载荷中没有 entries（AIHOT 页面结构可能已改版）")
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
        models = model_caps_service.parse_models_json(account.models_json, include_disabled=False)
        agent_id = None
        if account.source == "agent" and account.agent_route_id:
            route = routes.get(account.agent_route_id)
            if route is not None:
                agent = agents.get(route.agent_id)
                agent_id = agent.agent_id if agent is not None else None
                for model in model_caps_service.parse_models_json(route.models_json, include_disabled=False):
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


def _latest_successful_benchmarks(db: Session) -> dict[str, dict[str, Any]]:
    """最近若干次测速里，每个模型的首条成功结果。

    早期实现扫描整张 benchmark_results 表再在 Python 里去重，测速历史增长后
    每次仪表盘/排行榜请求都要全表重算（2 万条结果时约 153 ms）。这里只回看
    最近 BENCHMARK_LOOKBACK_RUNS 次运行，`created_at DESC, id DESC` 的排序语义不变。
    """
    recent_runs = (
        select(BenchmarkRun.id)
        .order_by(BenchmarkRun.created_at.desc(), BenchmarkRun.id.desc())
        .limit(BENCHMARK_LOOKBACK_RUNS)
        .scalar_subquery()
    )
    rows = db.execute(
        select(BenchmarkResult, BenchmarkRun.created_at)
        .join(BenchmarkRun, BenchmarkRun.id == BenchmarkResult.run_id)
        .where(
            BenchmarkResult.ok.is_(True),
            BenchmarkResult.output_tokens_per_second.is_not(None),
            BenchmarkResult.output_tokens_per_second > 0,
            BenchmarkResult.run_id.in_(recent_runs),
        )
        .order_by(BenchmarkRun.created_at.desc(), BenchmarkResult.id.desc())
    ).all()
    latest: dict[str, dict[str, Any]] = {}
    for result, created_at in rows:
        key = canonical_model_key(result.model)
        if not key or key in latest:
            continue
        latest[key] = {
            "model": result.model,
            "account_name": result.account_name,
            "provider": result.provider,
            "output_tokens_per_second": float(result.output_tokens_per_second or 0),
            "first_token_ms": result.first_token_ms,
            "total_ms": result.total_ms,
            "created_at": created_at,
        }
    return latest


def attach_benchmark_results(db: Session, items: list[dict[str, Any]], *, public: bool = False) -> None:
    latest = _latest_successful_benchmarks(db)
    if not latest:
        return
    for item in items:
        keys = entry_match_keys(item)
        payload: dict[str, Any] | None = None
        for key in keys:
            found = latest.get(key)
            if found is None:
                continue
            payload = dict(found)
            if public:
                payload["account_name"] = mask_public_label(payload.get("account_name"))
            break
        item["benchmark"] = payload


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
    attach_benchmark_results(db, items, public=public)
    if public:
        for item in items:
            item["local_matches"] = mask_local_matches(item.get("local_matches") or [])
    message = error_message or (snapshot.error_message if snapshot else None)
    if public and message:
        # 公开页只说明同步异常，不下发内部解析细节
        message = "榜单同步失败，当前展示最近一次缓存"
    return {
        "source_url": settings.aihot_leaderboard_url,
        "source_page": settings.aihot_leaderboard_url,
        "fetched_at": snapshot.fetched_at if snapshot else None,
        "stale": stale,
        "ttl_seconds": max(60, get_job_int("leaderboard", "interval_seconds", settings.aihot_leaderboard_ttl_seconds)),
        "min_refresh_seconds": max(0, settings.aihot_leaderboard_min_refresh_seconds),
        "source_updated_label": snapshot.source_updated_label if snapshot else None,
        "error_message": message,
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


def record_snapshot_error(db: Session, message: str) -> None:
    """把最近一次刷新失败写回快照。

    管理端/公开页只读缓存，不写回来的话页面只能看到缓存时间变旧，看不出是拉取失败。
    下次拉取成功时 save_snapshot 会把 error_message 清空。
    """
    snapshot = _latest_snapshot(db)
    if snapshot is None:
        return
    snapshot.error_message = message
    db.commit()


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
        # 这里刻意不丢线程池：payload 组装是 GIL 绑定的 Python 计算，实测 5 并发下
        # 丢线程池反而更慢（35 ms vs 14 ms，线程切换 + 连接争用）。
        # 有效的手段是收窄查询范围，见 BENCHMARK_LOOKBACK_RUNS。
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
            record_snapshot_error(db, str(error))
            return snapshot_to_payload(db, snapshot, stale=True, error_message=str(error))
        raise

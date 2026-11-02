from __future__ import annotations

from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.config import get_settings
from app.info import search_index

from .errors import WebError
from .providers import SearchProvider, SearchResult, SearxngProvider

RRF_K = 60
SCOPES = {"auto", "local", "web"}


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def _as_dict(value: SearchResult | dict[str, Any], fallback_provider: str) -> dict[str, Any]:
    data = asdict(value) if isinstance(value, SearchResult) else dict(value)
    data.setdefault("provider", fallback_provider)
    data.setdefault("title", "")
    data.setdefault("url", "")
    data.setdefault("snippet", "")
    data.setdefault("source", "")
    data.setdefault("published_at", None)
    return data


def _rrf(lists: list[list[dict[str, Any]]], top_k: int, *, local_boost: float) -> list[dict[str, Any]]:
    scores: dict[str, float] = {}
    items: dict[str, dict[str, Any]] = {}
    for results in lists:
        for rank, item in enumerate(results, start=1):
            key = _normalize_url(item.get("url", "")) or str(item.get("item_id") or "") or f"{id(item)}:{rank}"
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
            items.setdefault(key, item)
    for key, item in items.items():
        if item.get("provider") == "local" and local_boost:
            scores[key] *= 1.0 + local_boost
    ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)[:top_k]
    return [{**items[key], "score": round(score, 6)} for key, score in ordered]


def resolve_provider() -> SearchProvider | None:
    name = get_settings().web_search_provider.strip().lower()
    if name == "searxng":
        return SearxngProvider()
    return None


async def search(
    db: Session,
    *,
    query: str,
    top_k: int = 8,
    time_range: str = "any",
    site: str = "",
    scope: str = "auto",
) -> dict[str, Any]:
    if scope not in SCOPES:
        raise WebError(f"scope 只能是 {', '.join(sorted(SCOPES))}")
    text = (query or "").strip()
    if not text:
        raise WebError("query 必填")

    settings = get_settings()
    warnings: list[str] = []
    providers: list[str] = []
    local: list[dict[str, Any]] = []
    external: list[dict[str, Any]] = []

    if scope in {"auto", "local"}:
        local = search_index.search(db, text, top_k=top_k, time_range=time_range, site=site)
        providers.append("local")

    if scope in {"auto", "web"}:
        provider = resolve_provider()
        if provider is None:
            if scope == "web":
                warnings.append("未配置外部搜索 provider，未返回联网结果")
        elif scope == "web" or len(local) < top_k:
            try:
                found = await provider.search(
                    text, top_k=top_k, time_range=time_range, site=site
                )
            except WebError as error:
                warnings.append(f"外部搜索失败: {error.message}")
            else:
                external = [_as_dict(item, provider.name) for item in found]
                providers.append(provider.name)

    results = _rrf(
        [local, external],
        top_k,
        local_boost=settings.web_local_search_boost if scope == "auto" else 0.0,
    )
    return {"results": results, "providers": providers, "warnings": warnings}

from __future__ import annotations

import httpx

from app.config import get_settings

from ..errors import WebError
from .base import SearchResult

TIME_RANGES = {"day", "week", "month", "year"}


class SearxngProvider:
    name = "searxng"

    async def search(
        self,
        query: str,
        *,
        top_k: int,
        time_range: str,
        site: str,
    ) -> list[SearchResult]:
        settings = get_settings()
        base = settings.web_searxng_base_url.rstrip("/")
        if not base:
            raise WebError("未配置 WEB_SEARXNG_BASE_URL", status_code=503, error_type="provider_misconfigured")
        text = query if not site else f"{query} site:{site}"
        params = {"q": text, "format": "json", "language": "zh-CN"}
        if time_range in TIME_RANGES:
            params["time_range"] = time_range
        try:
            async with httpx.AsyncClient(timeout=settings.web_search_timeout_seconds) as client:
                response = await client.get(f"{base}/search", params=params)
        except httpx.HTTPError as error:
            raise WebError(f"SearXNG 请求失败: {error}", status_code=502, error_type="upstream_error") from error
        if response.status_code >= 400:
            raise WebError(
                f"SearXNG 返回 HTTP {response.status_code}",
                status_code=502,
                error_type="upstream_error",
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise WebError("SearXNG 返回非 JSON，请确认实例已开启 json 格式", error_type="provider_misconfigured") from error
        rows = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        results: list[SearchResult] = []
        for index, row in enumerate(rows[:top_k]):
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").strip()
            if not url:
                continue
            results.append(
                SearchResult(
                    title=str(row.get("title") or "").strip(),
                    url=url,
                    snippet=str(row.get("content") or "").strip(),
                    source=str(row.get("engine") or ""),
                    published_at=row.get("publishedDate"),
                    score=1.0 / (index + 1),
                    provider=self.name,
                )
            )
        return results

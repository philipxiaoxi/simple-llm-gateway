from __future__ import annotations

from .base import SearchResult


class NoneProvider:
    name = "none"

    async def search(
        self,
        query: str,
        *,
        top_k: int,
        time_range: str,
        site: str,
    ) -> list[SearchResult]:
        return []

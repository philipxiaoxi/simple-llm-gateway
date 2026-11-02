from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    source: str = ""
    published_at: str | None = None
    score: float = 0.0
    provider: str = ""


class SearchProvider(Protocol):
    name: str

    async def search(
        self,
        query: str,
        *,
        top_k: int,
        time_range: str,
        site: str,
    ) -> list[SearchResult]: ...

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.capabilities.douyin.resolver import Target


@dataclass
class ExtractedMedia:
    kind: str
    url: str
    width: int | None = None
    height: int | None = None
    duration_ms: int = 0


@dataclass
class ExtractedWork:
    extractor: str
    aweme_id: str = ""
    title: str = ""
    author_name: str = ""
    author_id: str = ""
    cover_url: str = ""
    duration_ms: int = 0
    kind: str = "video"
    media: list[ExtractedMedia] = field(default_factory=list)


class Extractor(Protocol):
    name: str

    def available(self) -> bool: ...

    def extract(self, target: Target) -> ExtractedWork: ...

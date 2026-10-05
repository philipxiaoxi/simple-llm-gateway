"""渠道适配器契约与上游字段解析工具。

上游的互动数据是带单位的字符串（`"1.65M"`、`"21.2K"`）、时长是 `"0:20"`
这类 mm:ss 文本，所以适配器层统一把它们规范化，下游只处理数字。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

_SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
_NUMBER = re.compile(r"^([\d.]+)([KkMmBb])?$")
_DURATION = re.compile(r"^(?:(\d+):)?(\d+):(\d+)$")

# 可展示的媒体类型。视频封面（poster）单独成行存放，但不算一条"可展示媒体"，
# 因此 media_count 与详情页轮播都用这个集合过滤。
DISPLAY_MEDIA_KINDS = ("image", "video")


def parse_count(value: object) -> int | None:
    """把 `"1.65M"` / `"21.2K"` / `"1,234"` / `1234` 解析成整数（带单位时为近似值）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    text = str(value).strip().replace(",", "").replace(" ", "")
    if not text:
        return None
    match = _NUMBER.match(text)
    if not match:
        return None
    try:
        number = float(match.group(1))
    except ValueError:
        return None
    suffix = (match.group(2) or "").lower()
    return int(number * _SUFFIX.get(suffix, 1))


def parse_duration_ms(value: object) -> int | None:
    """把 `"0:20"` / `"1:02:03"` 解析成毫秒；纯数字按秒处理。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text) * 1000
    match = _DURATION.match(text)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


def parse_datetime(value: object) -> datetime | None:
    """解析上游 ISO8601 时间（`2026-08-26T19:12:34+00:00`），并转成朴素 UTC。"""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


@dataclass(frozen=True)
class FetchedMedia:
    kind: str  # image | video | poster
    remote_url: str
    duration_ms: int | None = None


@dataclass(frozen=True)
class FetchedPost:
    external_id: str
    text: str
    published_at: datetime | None
    permalink: str
    author_name: str
    source_type: str
    views_text: str | None
    reactions: list[dict]
    is_forwarded: bool
    link_preview: dict | None
    media: list[FetchedMedia]


@dataclass(frozen=True)
class FetchedPage:
    posts: list[FetchedPost]
    after_cursor: int | None
    before_cursor: int | None
    # 是否还有更老的消息可翻（首次回填向更老翻页时用）
    has_more_before: bool | None = None
    # 上游在同一响应里带回的频道信息，采集时顺便刷新渠道元数据
    channel: SourcePreview | None = None


@dataclass(frozen=True)
class SourcePreview:
    identifier: str
    title: str
    username: str
    description: str
    avatar_url: str
    subscriber_count_text: str


class SourceAdapter(Protocol):
    kind: str

    def available(self) -> bool: ...

    def normalize(self, raw: str) -> str: ...

    def preview(self, identifier: str) -> SourcePreview: ...

    def fetch(
        self,
        identifier: str,
        *,
        after: int | None,
        limit: int,
        before: int | None = None,
    ) -> FetchedPage: ...


def classify(media: list[FetchedMedia]) -> str:
    """按媒体构成判定条目类型：text / image / video / mixed。"""
    has_video = any(item.kind == "video" for item in media)
    has_image = any(item.kind == "image" for item in media)
    if has_video and has_image:
        return "mixed"
    if has_video:
        return "video"
    if has_image:
        return "image"
    return "text"


def build_excerpt(text: str, limit: int = 200) -> str:
    """剥离链接、压缩空白后的摘要。"""
    cleaned = re.sub(r"https?://\S+", " ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "…"


def reactions_total(reactions: list[dict]) -> int | None:
    """表情回应总数；全部无法解析时返回 None。"""
    if not reactions:
        return None
    total = 0
    parsed_any = False
    for entry in reactions:
        if not isinstance(entry, dict):
            continue
        value = parse_count(entry.get("count"))
        if value is None:
            continue
        total += value
        parsed_any = True
    return total if parsed_any else None


def normalize_reactions(reactions: object) -> list[dict]:
    """只保留展示需要的字段，避免把上游整段结构存进库里。"""
    if not isinstance(reactions, list):
        return []
    cleaned: list[dict] = []
    for entry in reactions:
        if not isinstance(entry, dict):
            continue
        cleaned.append(
            {
                "emoji": str(entry.get("emoji") or ""),
                "count": str(entry.get("count") or ""),
                "paid": bool(entry.get("paid")),
            }
        )
    return cleaned


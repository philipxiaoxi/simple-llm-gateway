from __future__ import annotations

from app.info.errors import InfoError

from .base import (
    DISPLAY_MEDIA_KINDS,
    FetchedMedia,
    FetchedPage,
    FetchedPost,
    SourceAdapter,
    SourcePreview,
    build_excerpt,
    classify,
    normalize_reactions,
    parse_count,
    parse_datetime,
    parse_duration_ms,
    reactions_total,
)
from .telegram import TelegramAdapter

_ADAPTERS: dict[str, type] = {
    TelegramAdapter.kind: TelegramAdapter,
}


def supported_kinds() -> list[str]:
    return sorted(_ADAPTERS)


def build_adapter(
    kind: str, *, base_url: str = "", api_key: str = "", timeout_seconds: int | None = None
) -> SourceAdapter:
    factory = _ADAPTERS.get((kind or "").strip().lower())
    if factory is None:
        raise InfoError(
            f"不支持的渠道类型: {kind}", status_code=400, error_type="unsupported_source_kind"
        )
    return factory(base_url=base_url, api_key=api_key, timeout_seconds=timeout_seconds)


__all__ = [
    "DISPLAY_MEDIA_KINDS",
    "FetchedMedia",
    "FetchedPage",
    "FetchedPost",
    "SourceAdapter",
    "SourcePreview",
    "TelegramAdapter",
    "build_adapter",
    "build_excerpt",
    "classify",
    "normalize_reactions",
    "parse_count",
    "parse_datetime",
    "parse_duration_ms",
    "reactions_total",
    "supported_kinds",
]

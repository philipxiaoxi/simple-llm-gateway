from __future__ import annotations

from app.capabilities.douyin.errors import DouyinError

from .base import ExtractedMedia, ExtractedWork, Extractor
from .tikhub import TikHubExtractor

__all__ = [
    "ExtractedMedia",
    "ExtractedWork",
    "Extractor",
    "TikHubExtractor",
    "build_extractor",
]


def build_extractor(tikhub_config: tuple[str, str] | None = None) -> TikHubExtractor:
    """构造 TikHub 提取器；未配置 API Key 时抛出明确错误。"""
    base_url, api_key = tikhub_config or ("", "")
    extractor = TikHubExtractor(api_key=api_key, base_url=base_url)
    if not extractor.available():
        raise DouyinError(
            "TikHub 未配置 API Key，请在管理页「TikHub 解析 API」中配置",
            status_code=400,
            error_type="extractor_unavailable",
        )
    return extractor

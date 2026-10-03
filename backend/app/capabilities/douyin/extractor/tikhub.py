from __future__ import annotations

from app.capabilities.douyin.errors import DouyinError
from app.capabilities.douyin.resolver import Target
from app.config import get_settings

from .aweme import build_work, find_aweme
from .base import ExtractedWork

DEFAULT_BASE_URL = "https://api.tikhub.io"
HYBRID_PATH = "/api/v1/hybrid/video_data"


class TikHubExtractor:
    """通过 TikHub 托管 API 解析抖音作品（含视频与图集）。

    平台侧的反爬/签名由 TikHub 处理，本机不实现任何绕过逻辑，只按用户提供的
    API Key 调用其公开 REST 接口。
    """

    name = "tikhub"

    def __init__(
        self, api_key: str = "", base_url: str = "", timeout_seconds: int | None = None
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL
        settings = get_settings()
        self._timeout = timeout_seconds or settings.douyin_tikhub_timeout_seconds

    def available(self) -> bool:
        return bool(self._api_key)

    def extract(self, target: Target) -> ExtractedWork:
        import httpx

        if not self._api_key:
            raise DouyinError("TikHub 未配置 API Key", status_code=400, error_type="extractor_unavailable")
        url = f"{self._base_url}{HYBRID_PATH}"
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        try:
            response = httpx.get(
                url,
                params={"url": target.url},
                headers=headers,
                timeout=self._timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as error:
            raise DouyinError(
                f"TikHub 请求失败: {error}", status_code=502, error_type="upstream_error"
            ) from error
        if response.status_code >= 400:
            raise DouyinError(
                f"TikHub 返回 HTTP {response.status_code}: {response.text[:200]}",
                status_code=502,
                error_type="upstream_error",
            )
        try:
            body = response.json()
        except ValueError as error:
            raise DouyinError("TikHub 返回非 JSON", status_code=502, error_type="upstream_error") from error
        if isinstance(body, dict) and body.get("success") is False:
            error_info = body.get("error") or body.get("message") or "未知错误"
            raise DouyinError(
                f"TikHub 解析失败: {error_info}", status_code=422, error_type="content_unavailable"
            )
        data = body.get("data") if isinstance(body, dict) else None
        aweme = find_aweme(data if data is not None else body)
        if aweme is None:
            raise DouyinError(
                "TikHub 未返回作品数据", status_code=422, error_type="content_unavailable"
            )
        return build_work(aweme, self.name)

"""TikHub 的 Telegram 频道适配器。

字段映射已用真实响应固化（见 `.monkeycode/specs/2026-10-04-info-collection/design.md`
的「已验证的上游结构」与 `backend/tests/fixtures/telegram_channel_photos.json`）。

上游几个需要注意的点：

- `views` / `reactions[].count` / `channel_info.subscribers` 都是**带单位的字符串**
  （`"1.65M"`、`"21.2K"`、`"9.4M"`），不是数字。
- `media.photos[]` 是**纯 URL 字符串数组**；`media.videos[]` 是 `{url, thumb, duration}`，
  `duration` 形如 `"0:20"`。
- 分页游标在 `data.pagination.after_cursor`（本页最大 post_id），增量采集直接回传。
- `media.has_unsupported` 实测不可靠（`durov` 的纯文本帖也为 `true`），不参与分类。
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.info.errors import InfoError
from app.info.urlguard import channel_permalink, normalize_channel

from .base import (
    FetchedMedia,
    FetchedPage,
    FetchedPost,
    SourcePreview,
    normalize_reactions,
    parse_datetime,
    parse_duration_ms,
)

DEFAULT_BASE_URL = "https://api.tikhub.io"
POSTS_PATH = "/api/v1/telegram/web/fetch_channel_posts"
INFO_PATH = "/api/v1/telegram/web/fetch_channel_info"


def _channel_preview(info: dict[str, Any]) -> SourcePreview:
    counters = info.get("counters") if isinstance(info.get("counters"), dict) else {}
    return SourcePreview(
        identifier=str(info.get("username") or ""),
        title=str(info.get("title") or ""),
        username=str(info.get("username") or ""),
        description=str(info.get("description") or ""),
        avatar_url=str(info.get("photo") or ""),
        subscriber_count_text=str(
            info.get("subscribers") or (counters or {}).get("subscribers") or ""
        ),
    )


def _map_media(raw: object) -> list[FetchedMedia]:
    if not isinstance(raw, dict):
        return []
    media: list[FetchedMedia] = []

    photos = raw.get("photos")
    if isinstance(photos, list):
        for entry in photos:
            url = entry if isinstance(entry, str) else None
            if not url and isinstance(entry, dict):
                url = entry.get("url")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                media.append(FetchedMedia(kind="image", remote_url=url))

    videos = raw.get("videos")
    if isinstance(videos, list):
        for entry in videos:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            thumb = entry.get("thumb")
            # 封面单独成一项，且紧邻其视频之前，便于详情页按顺序配对
            if isinstance(thumb, str) and thumb.startswith(("http://", "https://")):
                media.append(FetchedMedia(kind="poster", remote_url=thumb))
            media.append(
                FetchedMedia(
                    kind="video",
                    remote_url=url,
                    duration_ms=parse_duration_ms(entry.get("duration")),
                )
            )

    return media


def map_message(raw: dict[str, Any], identifier: str) -> FetchedPost | None:
    """把一条上游消息映射成 FetchedPost；无文本且无媒体时返回 None（丢弃）。"""
    raw_id = raw.get("id")
    if raw_id is None:
        return None

    text = str(raw.get("text") or "")
    media = _map_media(raw.get("media"))
    if not text.strip() and not media:
        # 表情包、服务消息、空转发：既不满足“必须有其一”，也无展示价值
        return None

    link_preview = raw.get("link_preview")
    if not isinstance(link_preview, dict):
        link_preview = None

    permalink = raw.get("url")
    if not isinstance(permalink, str) or not permalink:
        permalink = channel_permalink(identifier, str(raw_id))

    views_text = raw.get("views")
    return FetchedPost(
        external_id=str(raw_id),
        text=text,
        published_at=parse_datetime(raw.get("date")),
        permalink=permalink,
        author_name=str(raw.get("author") or ""),
        source_type=str(raw.get("type") or ""),
        views_text=str(views_text) if views_text is not None else None,
        reactions=normalize_reactions(raw.get("reactions")),
        is_forwarded=bool(raw.get("is_forwarded")),
        link_preview=link_preview,
        media=media,
    )


class TelegramAdapter:
    kind = "telegram"

    def __init__(
        self, *, base_url: str = "", api_key: str = "", timeout_seconds: int | None = None
    ) -> None:
        self._base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL
        self._api_key = (api_key or "").strip()
        self._timeout = timeout_seconds or get_settings().info_http_timeout_seconds

    def available(self) -> bool:
        return bool(self._api_key)

    def normalize(self, raw: str) -> str:
        return normalize_channel(raw)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        import httpx

        if not self._api_key:
            raise InfoError(
                "TikHub 未配置 API Key", status_code=400, error_type="provider_unavailable"
            )
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        try:
            response = httpx.get(
                url, params=params, headers=headers, timeout=self._timeout, follow_redirects=True
            )
        except httpx.HTTPError as error:
            raise InfoError(
                f"TikHub 请求失败: {error}", status_code=502, error_type="upstream_error"
            ) from error

        if response.status_code in {401, 403}:
            raise InfoError(
                "TikHub 鉴权失败（401/403），请检查 API Key",
                status_code=502,
                error_type="provider_unauthorized",
            )
        if response.status_code >= 400:
            raise InfoError(
                f"TikHub 返回 HTTP {response.status_code}: {response.text[:200]}",
                status_code=502,
                error_type="upstream_error",
            )
        try:
            body = response.json()
        except ValueError as error:
            raise InfoError("TikHub 返回非 JSON", status_code=502, error_type="upstream_error") from error
        if isinstance(body, dict) and body.get("success") is False:
            raise InfoError(
                f"TikHub 返回失败: {body.get('message') or body.get('detail') or '未知错误'}",
                status_code=502,
                error_type="upstream_error",
            )
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise InfoError("TikHub 响应缺少 data", status_code=502, error_type="upstream_error")
        return data

    def preview(self, identifier: str) -> SourcePreview:
        data = self._get(INFO_PATH, {"channel": identifier})
        # 上游对不存在 / 未开放网页预览的频道会给出明确标记，不能只看有没有 username
        if data.get("exists") is False or data.get("preview_available") is False:
            raise InfoError(
                "频道不存在或未开放网页预览", status_code=422, error_type="channel_unavailable"
            )
        info = data.get("channel_info") if isinstance(data.get("channel_info"), dict) else data
        if not isinstance(info, dict) or not info.get("username"):
            raise InfoError(
                "频道不存在或不是公开频道", status_code=422, error_type="channel_unavailable"
            )
        return _channel_preview(info)

    def fetch(
        self,
        identifier: str,
        *,
        after: int | str | None,
        limit: int,
        before: int | str | None = None,
    ) -> FetchedPage:
        params: dict[str, Any] = {"channel": identifier, "limit": max(1, min(100, int(limit)))}
        # after = 取更新的消息；before = 取更老的消息（首次回填向历史翻页用）
        if after:
            params["after"] = int(after)
        if before:
            params["before"] = int(before)
        data = self._get(POSTS_PATH, params)

        messages = data.get("messages")
        posts: list[FetchedPost] = []
        if isinstance(messages, list):
            for entry in messages:
                if not isinstance(entry, dict):
                    continue
                mapped = map_message(entry, identifier)
                if mapped is not None:
                    posts.append(mapped)

        pagination = data.get("pagination") if isinstance(data.get("pagination"), dict) else {}
        channel_info = data.get("channel_info")
        has_more_before = pagination.get("has_more_before")
        return FetchedPage(
            posts=posts,
            after_cursor=_as_int(pagination.get("after_cursor")),
            before_cursor=_as_int(pagination.get("before_cursor")),
            has_more_before=bool(has_more_before) if has_more_before is not None else None,
            channel=_channel_preview(channel_info) if isinstance(channel_info, dict) else None,
        )


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None

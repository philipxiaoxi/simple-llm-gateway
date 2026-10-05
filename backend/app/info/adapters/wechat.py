"""TikHub 的微信公众号适配器（wechat_mp v2）。

上游接口（复用与 Telegram 相同的 TikHub Key）：

- `fetch_account_profile`：公众号资料（昵称 / 原创数 / IP 属地）。简介与头像恒为 null。
- `fetch_account_articles`：**单页**历史发文 + opaque `next_offset` 游标 + `is_end`。
- `fetch_article_detail_h5`：文章正文（推荐，字段最全），含 `content_text` 与正文 HTML。

实测要点（见 `.monkeycode/specs/2026-10-05-wechat-source/design.md`）：

- 列表 `page_size` 被上游忽略，翻页只能靠 `offset`（回传上一页 `next_offset`）。
- 微信接口较慢，超时需 ≥30 秒，否则会「已扣费但收不到响应」。
- 一篇文章的稳定标识为 `app_msg_id` + `idx`（同一次群发可含多篇）。
- 正文图片要从 `content.content_noencode`（HTML）里解析，主机限微信图床。
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app.config import get_settings
from app.info.errors import InfoError
from app.info.urlguard import (
    WECHAT_MEDIA_HOSTS,
    is_allowed_host,
    normalize_wechat,
    wechat_article_url,
)

from .base import (
    FetchedArticle,
    FetchedMedia,
    FetchedPage,
    FetchedPost,
    SourcePreview,
)

DEFAULT_BASE_URL = "https://api.tikhub.io"
PROFILE_PATH = "/api/v1/wechat_mp/v2/fetch_account_profile"
ARTICLES_PATH = "/api/v1/wechat_mp/v2/fetch_account_articles"
DETAIL_PATH = "/api/v1/wechat_mp/v2/fetch_article_detail_h5"
# 上游响应慢，低于 30 秒可能扣费却拿不到结果
MIN_TIMEOUT_SECONDS = 30

_BODY_IMG = re.compile(r"<img[^>]+?(?:data-src|src)=\"([^\"]+)\"", re.IGNORECASE)


def _parse_timestamp(value: object) -> datetime | None:
    """把上游的秒级时间戳转成朴素 UTC。"""
    try:
        timestamp = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, UTC).replace(tzinfo=None)


def _cover_media(raw: dict[str, Any]) -> list[FetchedMedia]:
    cover = raw.get("cover")
    if isinstance(cover, str) and cover.startswith(("http://", "https://")):
        return [FetchedMedia(kind="image", remote_url=cover)]
    return []


def map_article(raw: dict[str, Any]) -> FetchedPost | None:
    """把列表页的一篇文章映射成 FetchedPost；缺标识时返回 None。"""
    app_msg_id = raw.get("app_msg_id")
    if app_msg_id is None:
        return None
    idx = raw.get("idx")
    # 用 '-' 连接：Python 的 int() 会把下划线当数字分隔符解析（"2667_1" → 26671），
    # 会让字符串标识被误当整数游标推进，破坏「微信不推进整数游标」的约定
    external_id = f"{app_msg_id}-{idx if idx is not None else 1}"

    title = str(raw.get("title") or "")
    digest = str(raw.get("digest") or "")
    # 列表页摘要常为空，回退标题，保证卡片有可读文本
    text = digest or title
    media = _cover_media(raw)
    if not text.strip() and not media:
        return None

    url = raw.get("url")
    return FetchedPost(
        external_id=external_id[:64],
        text=text,
        published_at=_parse_timestamp(raw.get("create_time")),
        permalink=str(url or "")[:512],
        author_name="",
        source_type="wechat",
        views_text=None,
        reactions=[],
        is_forwarded=False,
        link_preview=None,
        media=media,
    )


def _body_images(content: dict[str, Any], *, limit: int) -> list[FetchedMedia]:
    """从正文 HTML 解析图片，只保留微信图床白名单内的地址并去重。"""
    html = content.get("content_noencode")
    if not isinstance(html, str) or not html:
        return []
    media: list[FetchedMedia] = []
    seen: set[str] = set()
    for url in _BODY_IMG.findall(html):
        # data-src 里的 &amp; 是 HTML 转义，还原成合法 URL 后再存，保证与正文匹配
        url = html_lib.unescape(url).strip()
        if not url.startswith(("http://", "https://")):
            continue
        dedup_key = url.split("#")[0]
        if dedup_key in seen:
            continue
        host = urlparse(url).hostname or ""
        if not is_allowed_host(host, WECHAT_MEDIA_HOSTS):
            continue
        seen.add(dedup_key)
        media.append(FetchedMedia(kind="image", remote_url=url))
        if len(media) >= limit:
            break
    return media


class WeChatMpAdapter:
    kind = "wechat"

    def __init__(
        self, *, base_url: str = "", api_key: str = "", timeout_seconds: int | None = None
    ) -> None:
        self._base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL
        self._api_key = (api_key or "").strip()
        configured = timeout_seconds or get_settings().info_http_timeout_seconds
        self._timeout = max(MIN_TIMEOUT_SECONDS, int(configured))

    def available(self) -> bool:
        return bool(self._api_key)

    def normalize(self, raw: str) -> str:
        return normalize_wechat(raw)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        if not self._api_key:
            raise InfoError(
                "TikHub 未配置 API Key", status_code=400, error_type="provider_unavailable"
            )
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=self._timeout)
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
            raise InfoError(
                "TikHub 返回非 JSON", status_code=502, error_type="upstream_error"
            ) from error
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

    def _article_content(self, url: str) -> dict[str, Any]:
        data = self._post(DETAIL_PATH, {"url": url, "raw": True})
        content = data.get("content")
        if not isinstance(content, dict):
            raise InfoError("文章详情缺少 content", status_code=502, error_type="upstream_error")
        return content

    def preview(self, identifier: str) -> SourcePreview:
        # 文章链接先反查 username，再用 username 拉资料
        username = identifier
        if wechat_article_url(identifier):
            content = self._article_content(identifier)
            username = str(content.get("user_name") or "").strip()
            if not username:
                raise InfoError(
                    "无法从文章解析出公众号", status_code=422, error_type="channel_unavailable"
                )

        data = self._post(PROFILE_PATH, {"username": username, "raw": False})
        resolved = str(data.get("user_name") or username).strip()
        if not resolved:
            raise InfoError(
                "公众号不存在或未开放", status_code=422, error_type="channel_unavailable"
            )
        nickname = str(data.get("nick_name") or "").strip()
        return SourcePreview(
            identifier=resolved,
            title=nickname or resolved,
            username=resolved,
            # 资料接口拿不到简介，恒为空
            description="",
            avatar_url="",
            subscriber_count_text=str(data.get("original_content_str") or "")[:16],
        )

    def fetch(
        self,
        identifier: str,
        *,
        after: int | str | None,
        limit: int,
        before: int | str | None = None,
    ) -> FetchedPage:
        payload: dict[str, Any] = {"username": identifier, "raw": False}
        if limit:
            payload["page_size"] = max(1, min(100, int(limit)))
        # after 对微信无意义（上游没有「取更新的」参数），增量靠最新页 + 去重
        if before:
            payload["offset"] = str(before)
        data = self._post(ARTICLES_PATH, payload)

        articles = data.get("articles")
        posts: list[FetchedPost] = []
        if isinstance(articles, list):
            for entry in articles:
                if not isinstance(entry, dict):
                    continue
                mapped = map_article(entry)
                if mapped is not None:
                    posts.append(mapped)

        is_end = bool(data.get("is_end"))
        next_offset = data.get("next_offset")
        before_cursor: str | None = None
        has_more_before: bool | None = None
        if not is_end and next_offset:
            before_cursor = str(next_offset)
            has_more_before = True
        elif is_end:
            has_more_before = False
        return FetchedPage(
            posts=posts,
            after_cursor=None,
            before_cursor=before_cursor,
            has_more_before=has_more_before,
            channel=None,
        )

    def fetch_article_post(self, url: str) -> tuple[FetchedPost, str]:
        """手动保存单篇：返回 (可直接入库的 FetchedPost, 原始正文 HTML)。

        text 取正文纯文字（缺失时退回标题），媒体为封面 + 正文图片（去重）。
        """
        content = self._article_content(url)
        limit = max(0, int(get_settings().info_wechat_max_body_images))
        media: list[FetchedMedia] = []
        seen: set[str] = set()

        cover = content.get("cdn_url")
        if isinstance(cover, str) and cover.startswith(("http://", "https://")):
            clean = html_lib.unescape(cover)
            seen.add(clean.split("#")[0])
            media.append(FetchedMedia(kind="image", remote_url=clean))
        for entry in _body_images(content, limit=limit):
            key = entry.remote_url.split("#")[0]
            if key in seen:
                continue
            seen.add(key)
            media.append(entry)

        mid = content.get("mid")
        idx = content.get("idx")
        if mid is not None:
            external_id = f"{mid}-{idx if idx is not None else 1}"
        else:
            external_id = "url-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:24]

        title = str(content.get("title") or "")
        text = str(content.get("content_text") or content.get("desc") or title)
        published = _parse_timestamp(
            content.get("create_timestamp") or content.get("ori_create_time")
        )
        permalink = str(content.get("link") or url)
        author = str(content.get("author") or content.get("nick_name") or "")
        post = FetchedPost(
            external_id=external_id[:64],
            text=text,
            published_at=published,
            permalink=permalink[:512],
            author_name=author[:128],
            source_type="wechat",
            views_text=None,
            reactions=[],
            is_forwarded=False,
            link_preview=None,
            media=media,
        )
        html = content.get("content_noencode")
        return post, (html if isinstance(html, str) else "")

    def fetch_article(self, url: str) -> FetchedArticle:
        """全文模式：返回正文文本、正文图片与原始 HTML（图片按配置上限截断）。"""
        content = self._article_content(url)
        text = str(
            content.get("content_text") or content.get("desc") or content.get("title") or ""
        )
        limit = max(0, int(get_settings().info_wechat_max_body_images))
        media = _body_images(content, limit=limit)
        html = content.get("content_noencode")
        return FetchedArticle(text=text, media=media, html=html if isinstance(html, str) else "")

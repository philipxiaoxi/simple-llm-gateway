"""通用 RSS / Atom 订阅适配器。

一个渠道 = 一个订阅地址。抓取整个 feed 后按 `guid` 去重，正文取
`description` / `content:encoded`（或 Atom 的 `summary` / `content`），入库时清洗为
HTML 保留排版。RSS 没有增量游标，靠 `(source_id, external_id)` 去重实现幂等。

图片不转存：媒体下载只允许 Telegram/微信图床，RSS 里的外链图片会在清洗时去掉，
避免详情页直连上游。

时间字段兼容 RSS 的 RFC822（`Wed, 07 Oct 2026 06:18:23 GMT`）与 Atom 的 ISO8601。
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from app.config import get_settings
from app.info.errors import InfoError
from app.info.urlguard import normalize_feed_url

from .base import FetchedPage, FetchedPost, SourcePreview, parse_datetime

_CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
_ATOM_NS = "http://www.w3.org/2005/Atom"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _https_get(url: str, timeout: int) -> str:
    import httpx

    headers = {
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        "User-Agent": "MonkeyCode-Info/1.0 (+local feed reader)",
    }
    try:
        response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as error:
        raise InfoError(
            f"订阅抓取失败: {error}", status_code=502, error_type="upstream_error"
        ) from error
    if response.status_code in {401, 403}:
        raise InfoError("订阅源拒绝访问（401/403）", status_code=502, error_type="provider_unauthorized")
    if response.status_code >= 400:
        raise InfoError(
            f"订阅源返回 HTTP {response.status_code}", status_code=502, error_type="upstream_error"
        )
    return response.text


def _parse_xml(text: str) -> ET.Element:
    try:
        return ET.fromstring(text.encode("utf-8", "ignore"))
    except ET.ParseError as error:
        raise InfoError("订阅源返回的内容不是有效 XML", status_code=502, error_type="upstream_error") from error


def _strip_html(value: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", value or "")).strip()


def _parse_feed_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC).replace(tzinfo=None)
        return parsed.replace(tzinfo=None)
    return parse_datetime(value)


def _external_id(guid: str, link: str) -> str:
    raw = (guid or link or "").strip()
    if not raw:
        return ""
    if len(raw) <= 64:
        return raw
    return hashlib.sha1(raw.encode()).hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element | None, name: str) -> str:
    if element is None:
        return ""
    for child in element:
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _atom_link(entry: ET.Element) -> str:
    fallback = ""
    for child in entry:
        if _local_name(child.tag) != "link":
            continue
        href = child.get("href") or ""
        if not href:
            continue
        if not fallback:
            fallback = href
        rel = (child.get("rel") or "alternate").lower()
        if rel == "alternate":
            return href
    return fallback


def _channel_preview(root: ET.Element) -> SourcePreview:
    if _local_name(root.tag) == "feed":  # Atom
        return SourcePreview(
            identifier="",
            title=_child_text(root, "title"),
            username="",
            description=_child_text(root, "subtitle"),
            avatar_url=_child_text(root, "icon") or _child_text(root, "logo"),
            subscriber_count_text="",
        )
    channel = root.find("channel")
    if channel is None:
        return SourcePreview("", "", "", "", "", "")
    image = channel.find("image")
    avatar = ""
    if image is not None:
        avatar = _child_text(image, "url")
    return SourcePreview(
        identifier="",
        title=_child_text(channel, "title"),
        username="",
        description=_child_text(channel, "description"),
        avatar_url=avatar,
        subscriber_count_text="",
    )


def _map_rss_item(item: ET.Element) -> FetchedPost | None:
    title = _child_text(item, "title")
    link = _child_text(item, "link")
    guid = _child_text(item, "guid")
    description = _child_text(item, "description")
    encoded = ""
    content_el = item.find(f"{{{_CONTENT_NS}}}encoded")
    if content_el is not None and content_el.text:
        encoded = content_el.text.strip()
    author = _child_text(item, "author") or _child_text(item, "creator")
    category = _child_text(item, "category")
    published = _parse_feed_datetime(_child_text(item, "pubDate"))

    return _build_post(
        title=title,
        link=link,
        guid=guid,
        summary_html=description or encoded,
        author=author,
        category=category,
        published=published,
    )


def _map_atom_entry(entry: ET.Element) -> FetchedPost | None:
    atom = f"{{{_ATOM_NS}}}"
    title = _child_text(entry, "title")
    link = _atom_link(entry)
    guid = (entry.findtext(f"{atom}id") or "").strip()
    summary = (entry.findtext(f"{atom}summary") or "").strip()
    content = (entry.findtext(f"{atom}content") or "").strip()
    author = ""
    author_el = entry.find(f"{atom}author")
    if author_el is not None:
        author = (author_el.findtext(f"{atom}name") or "").strip()
    published = _parse_feed_datetime(
        (entry.findtext(f"{atom}published") or entry.findtext(f"{atom}updated") or "").strip()
    )

    return _build_post(
        title=title,
        link=link,
        guid=guid,
        summary_html=content or summary,
        author=author,
        category="",
        published=published,
    )


def _build_post(
    *,
    title: str,
    link: str,
    guid: str,
    summary_html: str,
    author: str,
    category: str,
    published: datetime | None,
) -> FetchedPost | None:
    external_id = _external_id(guid, link)
    summary_text = _strip_html(summary_html)
    text = f"{title}\n\n{summary_text}".strip() if title else summary_text
    if not text:
        return None
    return FetchedPost(
        external_id=external_id,
        text=text,
        published_at=published,
        permalink=link or guid,
        author_name=author,
        source_type=category,
        views_text=None,
        reactions=[],
        is_forwarded=False,
        link_preview=None,
        media=[],
        html=summary_html,
    )


class RssAdapter:
    kind = "rss"

    def __init__(
        self, *, base_url: str = "", api_key: str = "", timeout_seconds: int | None = None
    ) -> None:
        self._timeout = timeout_seconds or get_settings().info_http_timeout_seconds

    def available(self) -> bool:
        return True

    def normalize(self, raw: str) -> str:
        return normalize_feed_url(raw)

    def _load(self, identifier: str) -> ET.Element:
        return _parse_xml(_https_get(identifier, self._timeout))

    def preview(self, identifier: str) -> SourcePreview:
        root = self._load(identifier)
        preview = _channel_preview(root)
        if not preview.title and _local_name(root.tag) not in {"rss", "feed"}:
            raise InfoError("无法识别该订阅源", status_code=422, error_type="channel_unavailable")
        return SourcePreview(
            identifier=identifier,
            title=preview.title or identifier,
            username="",
            description=preview.description,
            avatar_url=preview.avatar_url,
            subscriber_count_text="",
        )

    def fetch(
        self,
        identifier: str,
        *,
        after: int | str | None,
        limit: int,
        before: int | str | None = None,
    ) -> FetchedPage:
        root = self._load(identifier)
        is_atom = _local_name(root.tag) == "feed"
        if is_atom:
            entries = [child for child in root if _local_name(child.tag) == "entry"]
            mapper = _map_atom_entry
        else:
            channel = root.find("channel")
            if channel is None:
                raise InfoError("订阅源缺少 channel 节点", status_code=502, error_type="upstream_error")
            entries = [child for child in channel if _local_name(child.tag) == "item"]
            mapper = _map_rss_item

        posts: list[FetchedPost] = []
        for entry in entries:
            mapped = mapper(entry)
            if mapped is not None:
                posts.append(mapped)
            if len(posts) >= max(1, int(limit)):
                break

        preview = _channel_preview(root)
        channel_preview = (
            SourcePreview(
                identifier=identifier,
                title=preview.title,
                username="",
                description=preview.description,
                avatar_url=preview.avatar_url,
                subscriber_count_text="",
            )
            if preview.title
            else None
        )
        return FetchedPage(
            posts=posts,
            after_cursor=None,
            before_cursor=None,
            has_more_before=False,
            channel=channel_preview,
        )

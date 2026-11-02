from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import trafilatura
from bs4 import BeautifulSoup

from app.capabilities.docparse.convert import convert_bytes
from app.capabilities.docparse.errors import DocParseError

from .errors import WebError

REMOVABLE_TAGS = ("script", "style", "noscript", "iframe", "svg", "form", "nav", "footer", "aside")


@dataclass
class Extracted:
    markdown: str
    title: str = ""
    author: str = ""
    published_at: str | None = None
    site_name: str = ""
    fallback: bool = False


def _iso_date(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    return text or None


def _metadata(raw: bytes, url: str):
    try:
        return trafilatura.extract_metadata(raw, default_url=url)
    except Exception:
        return None


def _fallback_markdown(raw: bytes) -> str:
    try:
        soup = BeautifulSoup(raw, "lxml")
    except Exception:
        return ""
    for tag in soup(REMOVABLE_TAGS):
        tag.decompose()
    try:
        result = convert_bytes("page.html", str(soup).encode("utf-8"))
    except DocParseError:
        return ""
    return result.markdown


def _title_from_html(raw: bytes) -> str:
    try:
        soup = BeautifulSoup(raw, "lxml")
    except Exception:
        return ""
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return ""


def html_to_markdown(raw: bytes, url: str, *, include_links: bool) -> Extracted:
    meta = _metadata(raw, url)
    markdown = ""
    try:
        markdown = (
            trafilatura.extract(
                raw,
                url=url,
                output_format="markdown",
                include_links=include_links,
                include_comments=False,
                include_tables=True,
                include_formatting=True,
            )
            or ""
        )
    except Exception:
        markdown = ""

    fallback = False
    if not markdown.strip():
        markdown = _fallback_markdown(raw)
        fallback = True
    if not markdown.strip():
        raise WebError("未能从页面提取正文", status_code=422, error_type="empty_content")

    title = str(getattr(meta, "title", "") or "").strip() or _title_from_html(raw)
    return Extracted(
        markdown=markdown.strip(),
        title=title,
        author=str(getattr(meta, "author", "") or "").strip(),
        published_at=_iso_date(getattr(meta, "date", None)),
        site_name=str(getattr(meta, "sitename", "") or "").strip(),
        fallback=fallback,
    )

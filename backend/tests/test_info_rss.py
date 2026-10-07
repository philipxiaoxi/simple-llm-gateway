"""通用 RSS / Atom 订阅渠道：字段映射、去重与正文清洗。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.info.adapters import RssAdapter
from app.info.errors import InfoError
from app.info.sanitize import sanitize_feed_html
from app.info.urlguard import normalize_feed_url

FIXTURES = Path(__file__).parent / "fixtures"

ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom 示例</title>
  <subtitle>一个 Atom 订阅</subtitle>
  <entry>
    <title>条目一</title>
    <link rel="alternate" href="https://example.com/1"/>
    <id>tag:example.com,2026:1</id>
    <updated>2026-10-07T06:00:00Z</updated>
    <summary>这是摘要</summary>
  </entry>
</feed>
"""


def _feed_text() -> str:
    return (FIXTURES / "rss_aihot.xml").read_text(encoding="utf-8")


def test_normalize_feed_url() -> None:
    assert normalize_feed_url(" https://aihot.news/feed.xml ") == "https://aihot.news/feed.xml"
    for bad in ["", "   ", "aihot.news/feed.xml", "ftp://x/feed"]:
        with pytest.raises(InfoError):
            normalize_feed_url(bad)
    # 私网地址必须拒绝（SSRF 防护）
    with pytest.raises(InfoError):
        normalize_feed_url("http://127.0.0.1/feed.xml")


def test_sanitize_feed_html_drops_images_keeps_links() -> None:
    cleaned = sanitize_feed_html(
        '<p>看 <a href="https://x.com/a">原文</a> <img src="https://evil.example/x.png"/></p>'
    )
    assert "<a" in cleaned
    assert "原文" in cleaned
    assert "<img" not in cleaned
    assert "evil.example" not in cleaned


def test_rss_adapter_maps_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.info.adapters.rss._https_get", lambda url, timeout: _feed_text()
    )
    adapter = RssAdapter()

    preview = adapter.preview("https://aihot.news/feed.xml")
    assert preview.title == "AIHOT — 精选"
    assert preview.avatar_url == "https://aihot.news/logo.png"

    page = adapter.fetch("https://aihot.news/feed.xml", after=None, limit=50)
    assert page.has_more_before is False
    assert [post.external_id for post in page.posts] == ["aaa", "bbb"]

    first = page.posts[0]
    assert "Mistral Large 4" in first.text
    assert "Arena 宣布" in first.text
    assert first.published_at == datetime(2026, 10, 7, 6, 18, 23)
    assert first.source_type == "AI 模型"
    assert first.permalink == "https://aihot.news/items/aaa"
    assert "阅读原文" in first.html


def test_rss_adapter_maps_atom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.info.adapters.rss._https_get", lambda url, timeout: ATOM_FEED
    )
    adapter = RssAdapter()

    preview = adapter.preview("https://example.com/atom.xml")
    assert preview.title == "Atom 示例"

    page = adapter.fetch("https://example.com/atom.xml", after=None, limit=10)
    assert len(page.posts) == 1
    post = page.posts[0]
    assert post.external_id == "tag:example.com,2026:1"
    assert post.text.startswith("条目一")
    assert post.permalink == "https://example.com/1"
    assert post.published_at == datetime(2026, 10, 7, 6, 0, 0)


def test_create_and_collect_rss_source(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.info.adapters.rss._https_get", lambda url, timeout: _feed_text()
    )
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: RssAdapter())

    created = client.post(
        "/api/admin/info/sources",
        headers=auth_headers,
        json={"raw": "https://aihot.news/feed.xml", "kind": "rss"},
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]
    assert created.json()["kind"] == "rss"
    assert created.json()["title"] == "AIHOT — 精选"

    result = client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)
    assert result.status_code == 200, result.text
    assert result.json()["created"] == 2

    # 幂等：同一订阅再采一次不新增
    again = client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)
    assert again.json()["created"] == 0

    from app.db import get_session_factory
    from app.models import InfoItem

    session = get_session_factory()()
    try:
        items = list(session.scalars(select(InfoItem).where(InfoItem.source_id == source_id)))
        assert len(items) == 2
        first = next(item for item in items if item.external_id == "aaa")
        assert first.content_html
        assert "<a" in first.content_html
        assert "<img" not in first.content_html
    finally:
        session.close()

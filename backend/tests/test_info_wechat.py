"""微信公众号信源：字段映射、游标、全文补全 worker。

字段映射用真实响应 fixture 固化（`wechat_*.json`，探针见 `scripts/wechat_mp_probe.py`）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.info.adapters import FetchedArticle, FetchedMedia, FetchedPage, FetchedPost, SourcePreview
from app.info.adapters.wechat import WeChatMpAdapter, map_article
from app.info.content_style import adapt_dark_theme
from app.info.errors import InfoError
from app.info.sanitize import sanitize_wechat_html
from app.info.urlguard import normalize_wechat, wechat_article_url

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 输入归一化


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("gh_363b924965e9", "gh_363b924965e9"),
        ("@gh_363b924965e9", "gh_363b924965e9"),
        ("gh_363b924965e9@app", "gh_363b924965e9@app"),
        ("rmrbwx", "rmrbwx"),
        ("https://mp.weixin.qq.com/s/AbCdEf", "https://mp.weixin.qq.com/s/AbCdEf"),
    ],
)
def test_normalize_wechat_accepts_common_forms(raw: str, expected: str) -> None:
    assert normalize_wechat(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "公众号昵称", "https://example.com/x", "!!!"])
def test_normalize_wechat_rejects_garbage(raw: str) -> None:
    with pytest.raises(InfoError):
        normalize_wechat(raw)


def test_wechat_article_url_extraction() -> None:
    assert wechat_article_url("看看 https://mp.weixin.qq.com/s/abc 不错") == "https://mp.weixin.qq.com/s/abc"
    assert wechat_article_url("gh_abc123") is None


# ---------------------------------------------------------------- 字段映射


def test_map_article_from_real_fixture() -> None:
    raw = load_fixture("wechat_account_articles.json")["articles"][0]
    post = map_article(raw)
    assert post is not None
    assert post.external_id == f'{raw["app_msg_id"]}-{raw["idx"]}'
    assert post.source_type == "wechat"
    assert post.published_at is not None and post.published_at.tzinfo is None
    # digest 为空时回退标题，保证卡片有文本
    assert post.text == (raw["digest"] or raw["title"])
    assert post.media and post.media[0].kind == "image"
    assert post.media[0].remote_url == raw["cover"]


def test_map_article_uses_digest_when_present() -> None:
    post = map_article({"app_msg_id": 1, "idx": 2, "title": "标题", "digest": "摘要"})
    assert post is not None and post.text == "摘要"
    assert post.external_id == "1-2"


def test_map_article_drops_without_id() -> None:
    assert map_article({"title": "无 id"}) is None


# ---------------------------------------------------------------- 适配器契约


def test_wechat_adapter_fetch_maps_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = load_fixture("wechat_account_articles.json")
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self) -> dict:
            return {"data": fixture}

    def fake_post(url, json=None, headers=None, timeout=None):  # type: ignore[no-untyped-def]
        captured.update(url=url, payload=json, headers=headers, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)
    adapter = WeChatMpAdapter(base_url="https://api.tikhub.io", api_key="unit-test-key")

    page = adapter.fetch("gh_363b924965e9", after=None, limit=10)
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["username"] == "gh_363b924965e9"
    assert payload["page_size"] == 10
    assert "offset" not in payload
    assert captured["url"] == "https://api.tikhub.io/api/v1/wechat_mp/v2/fetch_account_articles"
    # after 对微信无意义，游标映射到 before 方向的 next_offset
    assert page.after_cursor is None
    assert page.before_cursor == str(fixture["next_offset"])
    assert page.has_more_before is True
    assert len(page.posts) == len(fixture["articles"])

    # 翻页：before 作为 offset 回传，且超时不低于 30 秒
    adapter.fetch("gh_363b924965e9", after=None, limit=10, before="CURSOR==")
    assert captured["payload"]["offset"] == "CURSOR=="  # type: ignore[index]
    assert captured["timeout"] >= 30


def test_wechat_adapter_requires_key() -> None:
    adapter = WeChatMpAdapter(base_url="https://api.tikhub.io", api_key="")
    assert adapter.available() is False
    with pytest.raises(InfoError) as caught:
        adapter.fetch("gh_363b924965e9", after=None, limit=5)
    assert caught.value.error_type == "provider_unavailable"


def test_wechat_fetch_article_returns_text_and_body_images(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = load_fixture("wechat_article_detail.json")

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self) -> dict:
            return {"data": fixture}

    monkeypatch.setattr(
        "httpx.post", lambda url, json=None, headers=None, timeout=None: FakeResponse()
    )
    adapter = WeChatMpAdapter(base_url="https://api.tikhub.io", api_key="k")
    article = adapter.fetch_article("https://mp.weixin.qq.com/s/x")

    assert article.text == fixture["content"]["content_text"]
    assert article.media and all(entry.kind == "image" for entry in article.media)
    assert all("mmbiz.qpic.cn" in entry.remote_url for entry in article.media)
    assert "<img" in article.html


# ---------------------------------------------------------------- 深色主题适配


def test_adapt_dark_theme_lightens_dark_text_and_drops_light_bg() -> None:
    out = adapt_dark_theme('<p style="color: #000000; background-color: #ffffff">x</p>')
    assert "#000000" not in out
    assert "background-color" not in out
    assert "color: hsl(" in out
    # 浅色底也处理 background 简写
    assert "background" not in adapt_dark_theme('<div style="background: #FFFFFF">x</div>').lower()


def test_adapt_dark_theme_keeps_light_text_and_unknown_values() -> None:
    out = adapt_dark_theme(
        '<span style="color: #ffffff">a</span><span style="color: var(--x)">b</span>'
    )
    assert "color: #ffffff" in out
    assert "var(--x)" in out


def test_adapt_dark_theme_lightens_rgba_black_and_keeps_dark_bg() -> None:
    out = adapt_dark_theme('<p style="color: rgba(0,0,0,0.9); background-color: #333333">x</p>')
    assert "rgba(0,0,0,0.9)" not in out
    assert "background-color: #333333" in out


# ---------------------------------------------------------------- 正文 HTML 清洗


def test_sanitize_wechat_html_strips_dangerous_content() -> None:
    raw = (
        '<p style="color:red" onclick="steal()">正文<script>alert(1)</script></p>'
        '<img src="https://mmbiz.qpic.cn/a.jpg" onerror="x()">'
        '<a href="javascript:alert(1)">链接</a>'
        '<iframe src="https://evil.com"></iframe>'
    )
    cleaned = sanitize_wechat_html(raw)
    assert "<script" not in cleaned and "alert(1)" not in cleaned
    assert "onclick" not in cleaned and "onerror" not in cleaned
    assert "<iframe" not in cleaned
    assert "javascript:alert" not in cleaned
    assert 'style="color:red"' in cleaned
    assert "https://mmbiz.qpic.cn/a.jpg" in cleaned
    assert "正文" in cleaned


def test_body_images_unescapes_html_entities(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = {
        "content": {
            "content_text": "正文",
            "content_noencode": (
                '<img data-src="https://mmbiz.qpic.cn/a.jpg?wx_fmt=png&amp;from=appmsg">'
            ),
        }
    }

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self) -> dict:
            return {"data": detail}

    monkeypatch.setattr(
        "httpx.post", lambda url, json=None, headers=None, timeout=None: FakeResponse()
    )
    adapter = WeChatMpAdapter(base_url="https://api.tikhub.io", api_key="k")
    article = adapter.fetch_article("https://mp.weixin.qq.com/s/x")
    # &amp; 应还原成 &，避免与正文正文不匹配
    assert article.media[0].remote_url == "https://mmbiz.qpic.cn/a.jpg?wx_fmt=png&from=appmsg"


def test_rewrite_content_images_handles_html_entities(client: TestClient) -> None:
    from app.info.items import _rewrite_content_images
    from app.models import InfoItem, InfoMedia

    item = InfoItem(id="i1", kind="image", text="", excerpt="")
    # 历史数据的 remote_url 是 &amp; 转义形态
    item.media = [
        InfoMedia(
            id="m1", item_id="i1", index_no=0, kind="image",
            remote_url="https://mmbiz.qpic.cn/a.jpg?x=1&amp;y=2", status="ready", purged=0,
        )
    ]
    html = '<p>x</p><img data-src="https://mmbiz.qpic.cn/a.jpg?x=1&y=2">'
    rewritten = _rewrite_content_images(html, item)
    assert "/api/admin/info/media/m1" in rewritten
    assert "data-src" not in rewritten


def test_rewrite_content_images_maps_ready_media(client: TestClient) -> None:
    from app.info.items import _rewrite_content_images
    from app.models import InfoItem, InfoMedia

    item = InfoItem(id="i1", kind="image", text="", excerpt="")
    ready = InfoMedia(
        id="m1", item_id="i1", index_no=0, kind="image",
        remote_url="https://mmbiz.qpic.cn/a.jpg", status="ready", purged=0,
    )
    item.media = [ready]
    html = '<p>x</p><img data-src="https://mmbiz.qpic.cn/a.jpg" class="wxw">'
    rewritten = _rewrite_content_images(html, item)
    assert "mmbiz.qpic.cn" not in rewritten
    assert "data-src" not in rewritten
    assert "/api/admin/info/media/m1" in rewritten


# ---------------------------------------------------------------- 采集与全文补全


class FakeWechatAdapter:
    kind = "wechat"

    def __init__(self, pages: list[FetchedPage]) -> None:
        self._pages = list(pages)
        self.calls: list[str | int | None] = []
        self.detail = FetchedArticle(
            text="补全后的正文",
            media=[FetchedMedia("image", "https://mmbiz.qpic.cn/b1.jpg")],
            html='<p style="color:red">正文段落</p><script>bad()</script>',
        )

    def available(self) -> bool:
        return True

    def normalize(self, raw: str) -> str:
        return normalize_wechat(raw)

    def preview(self, identifier: str) -> SourcePreview:
        return SourcePreview(
            identifier=identifier,
            title="测试公众号",
            username=identifier,
            description="",
            avatar_url="",
            subscriber_count_text="3 篇原创内容",
        )

    def fetch(self, identifier: str, *, after=None, limit=10, before=None) -> FetchedPage:
        self.calls.append(before)
        if self._pages:
            return self._pages.pop(0)
        return FetchedPage(posts=[], after_cursor=None, before_cursor=None, has_more_before=False)

    def fetch_article(self, url: str) -> FetchedArticle:
        return self.detail


def _wechat_post() -> FetchedPost:
    return FetchedPost(
        external_id="2667-1",
        text="列表摘要",
        published_at=datetime(2026, 10, 5, 9, 0),
        permalink="https://mp.weixin.qq.com/s/x",
        author_name="",
        source_type="wechat",
        views_text=None,
        reactions=[],
        is_forwarded=False,
        link_preview=None,
        media=[FetchedMedia("image", "https://mmbiz.qpic.cn/cover.jpg")],
    )


def _wechat_page(*, has_more: bool = False, cursor: str | None = None) -> FetchedPage:
    return FetchedPage(
        posts=[_wechat_post()],
        after_cursor=None,
        before_cursor=cursor,
        has_more_before=has_more,
    )


def test_wechat_second_collect_is_incremental_not_backfill(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    # 首次回填会沿 before 翻两页；成功后应用 last_success_at 判定，不再重复回填
    fake = FakeWechatAdapter([_wechat_page(has_more=True, cursor="C1"), _wechat_page()])
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: fake)

    created = client.post(
        "/api/admin/info/sources",
        headers=auth_headers,
        json={"raw": "gh_363b924965e9", "kind": "wechat"},
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]
    assert created.json()["kind"] == "wechat"

    first = client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)
    assert first.status_code == 200, first.text
    assert fake.calls == [None, "C1"]

    second = client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)
    assert second.status_code == 200, second.text
    # 第二轮只发一次（增量），before 为 None，且游标仍为 None（字符串渠道不推进整数游标）
    assert fake.calls == [None, "C1", None]
    source = client.get("/api/admin/info/sources", headers=auth_headers).json()["sources"][0]
    assert source["cursor_after"] is None
    assert source["last_success_at"] is not None


def test_wechat_worker_fills_fulltext_then_ai_pending(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    fake = FakeWechatAdapter([_wechat_page()])
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: fake)

    created = client.post(
        "/api/admin/info/sources",
        headers=auth_headers,
        json={"raw": "gh_363b924965e9", "kind": "wechat"},
    )
    source_id = created.json()["id"]
    collected = client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)
    assert collected.status_code == 200 and collected.json()["created"] == 1

    from app.db import get_session_factory
    from app.models import InfoItem
    from app.services import info_loop

    session = get_session_factory()()
    try:
        item = session.scalars(select(InfoItem)).one()
        assert item.content_status == "pending"
        assert item.text == "列表摘要"
        item_id = item.id
    finally:
        session.close()

    result = info_loop.enrich_wechat_content_once()
    assert result == {"processed": 1, "done": 1, "failed": 0}

    session = get_session_factory()()
    try:
        item = session.get(InfoItem, item_id)
        assert item is not None
        assert item.content_status == "done"
        assert item.text == "补全后的正文"
        urls = {row.remote_url for row in item.media}
        assert "https://mmbiz.qpic.cn/cover.jpg" in urls
        assert "https://mmbiz.qpic.cn/b1.jpg" in urls
        # 正文 HTML 已清洗：保留排版、去掉脚本
        assert "<p" in (item.content_html or "")
        assert "<script" not in (item.content_html or "")
    finally:
        session.close()


def test_fetch_article_post_maps_from_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = load_fixture("wechat_article_detail.json")

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self) -> dict:
            return {"data": fixture}

    monkeypatch.setattr(
        "httpx.post", lambda url, json=None, headers=None, timeout=None: FakeResponse()
    )
    adapter = WeChatMpAdapter(base_url="https://api.tikhub.io", api_key="k")
    post, html = adapter.fetch_article_post("https://mp.weixin.qq.com/s/x")

    content = fixture["content"]
    assert post.external_id == f'{content["mid"]}-{content["idx"]}'
    assert post.text == content["content_text"]
    assert post.author_name == content["author"]
    assert post.published_at is not None and post.published_at.tzinfo is None
    assert post.media and post.media[0].kind == "image"
    assert "<img" in html


class FakeArticleAdapter:
    kind = "wechat"

    def normalize(self, raw: str) -> str:
        return raw

    def fetch_article_post(self, url: str) -> tuple[FetchedPost, str]:
        return _wechat_post(), '<p style="color: #000000">正文段落</p>'


def test_save_single_article_into_other_source(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: FakeArticleAdapter())

    response = client.post(
        "/api/admin/info/articles",
        headers=auth_headers,
        json={"url": "https://mp.weixin.qq.com/s/x"},
    )
    assert response.status_code == 201, response.text

    sources_payload = client.get("/api/admin/info/sources", headers=auth_headers).json()["sources"]
    manual = [row for row in sources_payload if row["kind"] == "manual"]
    assert len(manual) == 1
    assert manual[0]["identifier"] == "other"
    assert manual[0]["title"] == "其他"
    assert manual[0]["poll_interval_seconds"] == 0
    assert manual[0]["item_count"] == 1
    assert response.json()["source_id"] == manual[0]["id"]

    # 再保存同一篇 → 冲突
    again = client.post(
        "/api/admin/info/articles",
        headers=auth_headers,
        json={"url": "https://mp.weixin.qq.com/s/x"},
    )
    assert again.status_code == 409


def test_save_article_rejects_non_wechat_url(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/admin/info/articles",
        headers=auth_headers,
        json={"url": "https://example.com/foo"},
    )
    assert response.status_code == 400


def test_rebuild_content_resets_wechat_items(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    fake = FakeWechatAdapter([_wechat_page()])
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: fake)

    created = client.post(
        "/api/admin/info/sources",
        headers=auth_headers,
        json={"raw": "gh_363b924965e9", "kind": "wechat"},
    )
    source_id = created.json()["id"]
    client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)

    from app.db import get_session_factory
    from app.models import InfoItem
    from app.services import info_loop

    assert info_loop.enrich_wechat_content_once()["done"] == 1

    response = client.post(
        f"/api/admin/info/sources/{source_id}/rebuild-content", headers=auth_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["reset"] == 1

    session = get_session_factory()()
    try:
        item = session.scalars(select(InfoItem)).one()
        assert item.content_status == "pending"
        assert item.content_html is None
        assert item.ai_status == "pending"
    finally:
        session.close()

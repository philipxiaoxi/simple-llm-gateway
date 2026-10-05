"""资讯收集的后端测试。

字段映射用两个真实响应 fixture 固化（`telegram_channel_posts.json` 来自 durov，
`telegram_channel_photos.json` 来自 telegram 官方频道），上游 HTTP 一律 mock，
测试不依赖网络与真实 TikHub 额度。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.clock import utcnow
from app.info import storage
from app.info import tokens as info_tokens
from app.info.adapters import (
    FetchedMedia,
    FetchedPage,
    FetchedPost,
    SourcePreview,
    build_excerpt,
    classify,
    parse_count,
    parse_duration_ms,
)
from app.info.adapters.telegram import TelegramAdapter, map_message
from app.info.errors import InfoError
from app.info.urlguard import MEDIA_HOSTS, assert_safe_url, normalize_channel

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 渠道标识


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://t.me/telegram", "telegram"),
        ("http://t.me/telegram/", "telegram"),
        ("t.me/telegram", "telegram"),
        ("https://t.me/s/telegram", "telegram"),
        ("@telegram", "telegram"),
        ("telegram", "telegram"),
        ("  这个频道不错 https://t.me/durov 你看看 ", "durov"),
    ],
)
def test_normalize_channel_accepts_common_forms(raw: str, expected: str) -> None:
    assert normalize_channel(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "https://example.com/foo", "!!!", "https://t.me/"])
def test_normalize_channel_rejects_garbage(raw: str) -> None:
    with pytest.raises(InfoError):
        normalize_channel(raw)


# ---------------------------------------------------------------- 上游字段解析


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.65M", 1_650_000),
        ("21.2K", 21_200),
        ("2M", 2_000_000),
        ("1,234", 1234),
        ("1234", 1234),
        (1234, 1234),
        ("", None),
        (None, None),
        ("abc", None),
    ],
)
def test_parse_count(raw: object, expected: int | None) -> None:
    assert parse_count(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0:20", 20_000), ("1:02:03", 3_723_000), ("20", 20_000), (20_000, 20_000), ("", None), (None, None), ("x", None)],
)
def test_parse_duration_ms(raw: object, expected: int | None) -> None:
    assert parse_duration_ms(raw) == expected


def test_classify_and_excerpt() -> None:
    assert classify([]) == "text"
    assert classify([FetchedMedia("image", "https://x/1.jpg")]) == "image"
    assert classify([FetchedMedia("video", "https://x/1.mp4")]) == "video"
    assert classify([FetchedMedia("poster", "https://x/p.jpg")]) == "text"
    mixed = [FetchedMedia("image", "https://x/1.jpg"), FetchedMedia("video", "https://x/1.mp4")]
    assert classify(mixed) == "mixed"

    excerpt = build_excerpt("看这个 https://example.com/a/b 很不错\n\n还有第二段")
    assert "https://example.com" not in excerpt
    assert "\n" not in excerpt
    assert excerpt.startswith("看这个")


# ---------------------------------------------------------------- 真实响应映射


def test_map_message_parses_views_and_reactions_from_real_fixture() -> None:
    messages = load_fixture("telegram_channel_posts.json")["data"]["messages"]
    raw = next(item for item in messages if item.get("views"))
    post = map_message(raw, "durov")
    assert post is not None
    assert post.external_id == str(raw["id"])
    assert post.views_text == raw["views"]
    assert parse_count(post.views_text) is not None
    assert post.permalink == raw["url"]
    assert post.published_at is not None and post.published_at.tzinfo is None
    # 上游 reactions 的 count 是带单位字符串，这里保留原文
    assert all("count" in entry for entry in post.reactions)


def test_map_message_photo_media_is_url_string_array() -> None:
    messages = load_fixture("telegram_channel_photos.json")["data"]["messages"]
    raw = next(item for item in messages if item.get("type") == "photo")
    post = map_message(raw, "telegram")
    assert post is not None
    # photos[] 是纯 URL 字符串数组（不是对象），映射后应是 image
    assert classify(post.media) == "image"
    assert post.media[0].kind == "image"
    assert post.media[0].remote_url == raw["media"]["photos"][0]


def test_map_message_video_emits_poster_then_video() -> None:
    messages = load_fixture("telegram_channel_photos.json")["data"]["messages"]
    raw = next(item for item in messages if item.get("type") == "video" and (item.get("media") or {}).get("videos"))
    post = map_message(raw, "telegram")
    assert post is not None
    assert [entry.kind for entry in post.media] == ["poster", "video"]
    video = raw["media"]["videos"][0]
    assert post.media[1].remote_url == video["url"]
    assert post.media[1].duration_ms == parse_duration_ms(video.get("duration"))
    assert classify(post.media) == "video"


def test_map_message_drops_messages_without_text_or_media() -> None:
    assert map_message({"id": 9, "text": "   ", "media": {"photos": [], "videos": []}}, "x") is None
    assert map_message({"id": 10, "text": "hi", "media": {}}, "x") is not None
    assert map_message({"id": 11, "text": "", "media": {"photos": ["https://cdn4.telesco.pe/a.jpg"]}}, "x") is not None


def test_telegram_adapter_fetch_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = load_fixture("telegram_channel_photos.json")
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self) -> dict:
            return fixture

    def fake_get(url, params=None, headers=None, timeout=None, follow_redirects=None):  # type: ignore[no-untyped-def]
        captured.update(url=url, params=params, headers=headers, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)
    adapter = TelegramAdapter(base_url="https://api.tikhub.io", api_key="unit-test-key")
    page = adapter.fetch("telegram", after=451, limit=10)

    assert str(captured["url"]).endswith("/api/v1/telegram/web/fetch_channel_posts")
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["channel"] == "telegram"
    assert params["limit"] == 10
    assert params["after"] == 451
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer unit-test-key"
    assert page.after_cursor == fixture["data"]["pagination"]["after_cursor"]
    assert page.channel is not None and page.channel.title


def test_telegram_adapter_requires_key() -> None:
    adapter = TelegramAdapter(base_url="https://api.tikhub.io", api_key="")
    assert adapter.available() is False
    with pytest.raises(InfoError) as caught:
        adapter.fetch("telegram", after=None, limit=5)
    assert caught.value.error_type == "provider_unavailable"


# ---------------------------------------------------------------- SSRF 防护


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x.jpg",
        "http://10.0.0.1/x.jpg",
        "http://169.254.169.254/latest/meta-data",
        "https://evil.com/x.jpg",
        "file:///etc/passwd",
        "not-a-url",
    ],
)
def test_assert_safe_url_blocks_unsafe_targets(url: str) -> None:
    with pytest.raises(InfoError):
        assert_safe_url(url, allow_hosts=MEDIA_HOSTS)


# ---------------------------------------------------------------- 采集流程


class FakeAdapter:
    kind = "telegram"

    def __init__(self, pages: list[FetchedPage]) -> None:
        self._pages = list(pages)
        self.calls: list[tuple[int | None, int | None]] = []

    def available(self) -> bool:
        return True

    def normalize(self, raw: str) -> str:
        return normalize_channel(raw)

    def preview(self, identifier: str) -> SourcePreview:
        return SourcePreview(
            identifier=identifier,
            title=f"频道 {identifier}",
            username=identifier,
            description="演示频道",
            avatar_url="https://cdn1.telesco.pe/avatar.jpg",
            subscriber_count_text="1.2K",
        )

    def fetch(
        self, identifier: str, *, after: int | None, limit: int, before: int | None = None
    ) -> FetchedPage:
        self.calls.append((after, before))
        if self._pages:
            return self._pages.pop(0)
        return FetchedPage(posts=[], after_cursor=after, before_cursor=None)


def _post(post_id: int, text: str = "", media: list[FetchedMedia] | None = None) -> FetchedPost:
    return FetchedPost(
        external_id=str(post_id),
        text=text,
        published_at=datetime(2026, 8, 26, 19, 12, 34),
        permalink=f"https://t.me/demochannel/{post_id}",
        author_name="Demo",
        source_type="",
        views_text=None,
        reactions=[],
        is_forwarded=False,
        link_preview=None,
        media=media or [],
    )


def _demo_page() -> FetchedPage:
    return FetchedPage(
        posts=[
            _post(100, text="纯文字内容"),
            _post(101, text="一张图", media=[FetchedMedia("image", "https://cdn4.telesco.pe/1.jpg")]),
            _post(
                102,
                text="一个视频",
                media=[
                    FetchedMedia("poster", "https://cdn4.telesco.pe/p.jpg"),
                    FetchedMedia("video", "https://cdn4.telesco.pe/1.mp4", duration_ms=20_000),
                ],
            ),
        ],
        after_cursor=102,
        before_cursor=100,
        # 回填向更老翻页时据此停止，避免测试里把后续排队的页也吃掉
        has_more_before=False,
        channel=SourcePreview("demochannel", "演示频道", "demochannel", "", "", "1.2K"),
    )


def _create_source(client: TestClient, auth_headers: dict[str, str], raw: str = "@demochannel") -> str:
    response = client.post("/api/admin/info/sources", headers=auth_headers, json={"raw": raw})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_info_routes_require_admin(client: TestClient) -> None:
    assert client.get("/api/admin/info/sources").status_code == 401
    assert client.get("/api/admin/info/items").status_code == 401
    assert client.get("/api/admin/info/stats").status_code == 401


def test_preview_and_create_source(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: FakeAdapter([]))

    preview = client.post(
        "/api/admin/info/sources/preview", headers=auth_headers, json={"raw": "https://t.me/demochannel"}
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["identifier"] == "demochannel"
    assert body["title"] == "频道 demochannel"
    assert body["already_added"] is False

    source_id = _create_source(client, auth_headers)

    # 重复添加同一渠道应 409
    again = client.post("/api/admin/info/sources", headers=auth_headers, json={"raw": "@demochannel"})
    assert again.status_code == 409

    listed = client.get("/api/admin/info/sources", headers=auth_headers).json()
    assert len(listed["sources"]) == 1
    assert listed["sources"][0]["id"] == source_id
    assert listed["sources"][0]["identifier"] == "demochannel"
    assert listed["sources"][0]["subscriber_count_text"] == "1.2K"


def test_collect_is_idempotent_and_advances_cursor(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    fake = FakeAdapter([_demo_page(), _demo_page()])
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: fake)
    source_id = _create_source(client, auth_headers)

    first = client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)
    assert first.status_code == 200, first.text
    assert first.json() == {"source_id": source_id, "fetched": 3, "created": 3, "skipped": 0, "error": None}

    # 同一页再采一次：游标已推进，全部命中唯一约束，不产生重复行
    second = client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)
    assert second.json()["created"] == 0
    assert second.json()["skipped"] == 3

    # 首次采集不带 after、不带 before，只取最新一页；之后带上次的游标
    assert fake.calls == [(None, None), (102, None)]

    source = client.get("/api/admin/info/sources", headers=auth_headers).json()["sources"][0]
    assert source["cursor_after"] == 102
    assert source["item_count"] == 3
    assert source["last_error"] is None
    assert source["consecutive_failures"] == 0


def test_collect_failure_keeps_cursor_and_records_error(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    class FailingAdapter(FakeAdapter):
        def fetch(
            self, identifier: str, *, after: int | None, limit: int, before: int | None = None
        ) -> FetchedPage:
            self.calls.append((after, before))
            raise InfoError("上游 401", status_code=502, error_type="provider_unauthorized")

    fake = FailingAdapter([])
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: fake)
    source_id = _create_source(client, auth_headers)

    # 契约：采集失败仍返回 200，失败信息放在 error 字段（前端据此给分类提示）
    response = client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["created"] == 0
    assert payload["error"]["type"] == "provider_unauthorized"

    source = client.get("/api/admin/info/sources", headers=auth_headers).json()["sources"][0]
    assert source["cursor_after"] is None
    assert source["consecutive_failures"] == 1
    assert "provider_unauthorized" in (source["last_error"] or "")


def test_backfill_pages_older_with_before_cursor(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    """首次采集必须用 before 游标向更老翻页，否则历史永远进不来。"""
    older = FetchedPage(
        posts=[_post(98, text="更老的一条"), _post(97, text="最老的一条")],
        after_cursor=98,
        before_cursor=97,
        has_more_before=False,
        channel=None,
    )
    newest = FetchedPage(
        posts=[_post(102, text="最新一条")],
        after_cursor=102,
        before_cursor=100,
        has_more_before=True,
        channel=SourcePreview("demochannel", "演示频道", "demochannel", "", "", "1.2K"),
    )
    fake = FakeAdapter([newest, older])
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: fake)
    source_id = _create_source(client, auth_headers)

    result = client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers).json()
    assert result["created"] == 3
    # 第一页不带 before，第二页回传上一页的 before_cursor
    assert fake.calls == [(None, None), (None, 100)]

    source = client.get("/api/admin/info/sources", headers=auth_headers).json()["sources"][0]
    # 游标推进到最新那条，后续轮询走 after 增量
    assert source["cursor_after"] == 102
    assert source["item_count"] == 3


def test_media_count_and_status_follow_media_health(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    """媒体失败或清理后，media_count 必须复算，且全失败时状态为 failed。"""
    page = FetchedPage(
        posts=[
            _post(
                200,
                text="两张图",
                media=[
                    FetchedMedia("image", "https://cdn4.telesco.pe/a.jpg"),
                    FetchedMedia("image", "https://cdn4.telesco.pe/b.jpg"),
                ],
            )
        ],
        after_cursor=200,
        before_cursor=200,
        has_more_before=False,
    )
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: FakeAdapter([page]))
    source_id = _create_source(client, auth_headers)
    client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)

    from app.db import get_session_factory
    from app.info import collector as collector_module
    from app.models import InfoItem, InfoMedia

    session = get_session_factory()()
    try:
        item = session.scalars(select(InfoItem)).one()
        rows = list(session.scalars(select(InfoMedia).order_by(InfoMedia.index_no)))
        # 刚入库时媒体还没有转存，计数已按可展示项算好
        assert item.media_count == 2 and item.status == "media_pending"

        # 两项都转存成功 -> ready
        rows[0].status = "ready"
        rows[1].status = "ready"
        collector_module.refresh_item_media_state(session, item)
        session.commit()
        assert item.media_count == 2 and item.status == "ready"

        # 一项失败 -> media_partial，计数降为 1
        rows[0].status = "failed"
        collector_module.refresh_item_media_state(session, item)
        session.commit()
        assert item.media_count == 1 and item.status == "media_partial"

        # 全部失败 -> failed，计数归零
        rows[1].status = "failed"
        collector_module.refresh_item_media_state(session, item)
        session.commit()
        assert item.media_count == 0 and item.status == "failed"
    finally:
        session.close()


def test_delete_source_keeps_content_by_default(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    """默认只删渠道：内容与媒体文件都保留，内容的 source 变成 null。"""
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: FakeAdapter([_demo_page()]))
    source_id = _create_source(client, auth_headers)
    client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)

    assert client.delete(f"/api/admin/info/sources/{source_id}", headers=auth_headers).status_code == 204
    assert client.get("/api/admin/info/sources", headers=auth_headers).json()["sources"] == []

    payload = client.get("/api/admin/info/items?limit=10", headers=auth_headers).json()
    assert payload["total"] == 3
    assert all(item["source"] is None for item in payload["items"])


def test_delete_source_with_purge_removes_content_and_files(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: FakeAdapter([_demo_page()]))
    source_id = _create_source(client, auth_headers)
    client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)

    from app.db import get_session_factory
    from app.info import storage
    from app.models import InfoItem

    session = get_session_factory()()
    try:
        item_id = session.scalars(select(InfoItem.id)).first()
    finally:
        session.close()
    assert item_id is not None
    probe = storage.media_path(item_id, "000.jpg")
    probe.write_bytes(b"x")

    response = client.delete(
        f"/api/admin/info/sources/{source_id}?purge_items=true", headers=auth_headers
    )
    assert response.status_code == 204
    assert client.get("/api/admin/info/items?limit=10", headers=auth_headers).json()["total"] == 0
    assert not probe.exists()


def test_search_escapes_like_wildcards(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    """用户输入的 % / _ 必须当字面量，不能变成"匹配任意"。"""
    page = FetchedPage(
        posts=[_post(300, text="进度 100% 完成"), _post(301, text="普通一条")],
        after_cursor=301,
        before_cursor=300,
        has_more_before=False,
    )
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: FakeAdapter([page]))
    source_id = _create_source(client, auth_headers)
    client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)

    wildcard = client.get("/api/admin/info/items?q=%25", headers=auth_headers).json()
    assert wildcard["total"] == 1
    assert "100%" in wildcard["items"][0]["text"]


def test_collected_items_shape_and_media_count(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: FakeAdapter([_demo_page()]))
    source_id = _create_source(client, auth_headers)
    client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)

    payload = client.get("/api/admin/info/items?limit=10", headers=auth_headers).json()
    assert payload["total"] == 3
    by_kind = {item["kind"]: item for item in payload["items"]}

    # 纯文字：无媒体、无封面，前端走 CSS 封面
    assert by_kind["text"]["media_count"] == 0
    assert by_kind["text"]["cover"] is None
    assert by_kind["text"]["cover_seed"] > 0

    # 单图：媒体数 1，媒体尚未转存因此封面为空
    assert by_kind["image"]["media_count"] == 1
    assert by_kind["image"]["cover"] is None
    assert by_kind["image"]["status"] == "media_pending"

    # 视频：poster 不计入 media_count
    assert by_kind["video"]["media_count"] == 1
    assert by_kind["video"]["status"] == "media_pending"

    # 封面种子稳定：同一条内容重复查询得到同一个 seed
    again = client.get("/api/admin/info/items?limit=10", headers=auth_headers).json()
    seeds = {item["id"]: item["cover_seed"] for item in payload["items"]}
    assert {item["id"]: item["cover_seed"] for item in again["items"]} == seeds


def test_items_filters_and_pagination(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: FakeAdapter([_demo_page()]))
    source_id = _create_source(client, auth_headers)
    client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)

    # 多类型过滤：图文 = image,mixed
    image_like = client.get("/api/admin/info/items?kind=image,mixed", headers=auth_headers).json()
    assert {item["kind"] for item in image_like["items"]} == {"image"}

    only_text = client.get("/api/admin/info/items?kind=text", headers=auth_headers).json()
    assert {item["kind"] for item in only_text["items"]} == {"text"}

    # 关键词命中正文
    searched = client.get("/api/admin/info/items?q=一个视频", headers=auth_headers).json()
    assert searched["total"] == 1

    # 游标分页：limit=2 时第一页返回 2 条并给出游标，第二页补齐且不重复
    page1 = client.get("/api/admin/info/items?limit=2", headers=auth_headers).json()
    assert len(page1["items"]) == 2 and page1["next_cursor"]
    page2 = client.get(f"/api/admin/info/items?limit=2&cursor={page1['next_cursor']}", headers=auth_headers).json()
    ids = {item["id"] for item in page1["items"]} | {item["id"] for item in page2["items"]}
    assert len(ids) == 3
    assert page2["next_cursor"] is None

    # 升序与降序返回相反的次序
    asc = client.get("/api/admin/info/items?order=asc&limit=10", headers=auth_headers).json()["items"]
    desc = client.get("/api/admin/info/items?order=desc&limit=10", headers=auth_headers).json()["items"]
    assert [item["id"] for item in asc] == list(reversed([item["id"] for item in desc]))

    # 升序翻页同样不重复
    asc1 = client.get("/api/admin/info/items?order=asc&limit=1", headers=auth_headers).json()
    asc2 = client.get(
        f"/api/admin/info/items?order=asc&limit=1&cursor={asc1['next_cursor']}", headers=auth_headers
    ).json()
    assert asc1["items"][0]["id"] != asc2["items"][0]["id"]


def test_favorite_and_hidden_toggle(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: FakeAdapter([_demo_page()]))
    source_id = _create_source(client, auth_headers)
    client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)
    items = client.get("/api/admin/info/items?limit=10", headers=auth_headers).json()["items"]
    target = items[0]["id"]

    # 前端发的显式目标态
    favored = client.post(
        f"/api/admin/info/items/{target}/favorite", headers=auth_headers, json={"favorite": True}
    )
    assert favored.status_code == 200
    assert favored.json()["is_favorite"] is True

    favorite_list = client.get("/api/admin/info/items?favorite=1", headers=auth_headers).json()
    assert [item["id"] for item in favorite_list["items"]] == [target]

    # 取消收藏
    client.post(f"/api/admin/info/items/{target}/favorite", headers=auth_headers, json={"favorite": False})
    assert client.get("/api/admin/info/items?favorite=1", headers=auth_headers).json()["total"] == 0

    # 不传 body 表示切换
    toggled = client.post(f"/api/admin/info/items/{target}/favorite", headers=auth_headers)
    assert toggled.json()["is_favorite"] is True

    # 隐藏后默认列表不再返回
    hidden = client.post(f"/api/admin/info/items/{target}/hidden", headers=auth_headers, json={"hidden": True})
    assert hidden.json()["is_hidden"] is True
    visible = client.get("/api/admin/info/items?limit=10", headers=auth_headers).json()
    assert target not in {item["id"] for item in visible["items"]}
    with_hidden = client.get("/api/admin/info/items?limit=10&include_hidden=true", headers=auth_headers).json()
    assert target in {item["id"] for item in with_hidden["items"]}


def test_item_detail_includes_media_and_poster(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: FakeAdapter([_demo_page()]))
    source_id = _create_source(client, auth_headers)
    client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)

    items = client.get("/api/admin/info/items?limit=10", headers=auth_headers).json()["items"]
    video = next(item for item in items if item["kind"] == "video")
    detail = client.get(f"/api/admin/info/items/{video['id']}", headers=auth_headers).json()

    # 轮播只列可展示媒体（不含 poster 行），poster 以 poster_url 挂到视频上
    assert [entry["kind"] for entry in detail["media"]] == ["video"]
    assert detail["media"][0]["duration_ms"] == 20_000
    # 尚未转存时没有地址，但状态可见
    assert detail["media"][0]["url"] is None
    assert detail["media"][0]["status"] == "pending"

    assert client.get("/api/admin/info/items/does-not-exist", headers=auth_headers).status_code == 404


# ---------------------------------------------------------------- 媒体分发


def _seed_ready_media(client: TestClient, *, purged: int = 0) -> tuple[str, bytes]:
    """造一条已转存的媒体，返回 (media_id, 文件字节)。"""
    from app.db import get_session_factory
    from app.models import InfoItem, InfoMedia, InfoSource

    session = get_session_factory()()
    try:
        now = utcnow()
        source = InfoSource(
            id="src-1", kind="telegram", identifier="demochannel", title="演示", created_at=now, updated_at=now
        )
        item = InfoItem(
            id="item-1",
            source_id="src-1",
            external_id="1",
            kind="image",
            text="x",
            excerpt="x",
            media_count=1,
            cover_seed=7,
            status="ready",
            collected_at=now,
            created_at=now,
        )
        media = InfoMedia(
            id="media-1",
            item_id="item-1",
            index_no=0,
            kind="image",
            remote_url="https://cdn4.telesco.pe/1.jpg",
            filename="000.jpg",
            content_type="image/jpeg",
            size_bytes=4,
            status="ready",
            purged=purged,
        )
        session.add_all([source, item, media])
        session.commit()
    finally:
        session.close()

    payload = b"\xff\xd8\xff\xd9"
    storage.media_path("item-1", "000.jpg").write_bytes(payload)
    return "media-1", payload


def test_media_route_requires_valid_token(client: TestClient) -> None:
    media_id, payload = _seed_ready_media(client)

    # 先验令牌再查库：未授权时无论 media_id 是否存在都是 401，不泄漏存在性
    assert client.get(f"/api/admin/info/media/{media_id}").status_code == 401
    assert client.get(f"/api/admin/info/media/{media_id}?token=bad").status_code == 401
    assert client.get("/api/admin/info/media/missing?token=bad").status_code == 401
    # 令牌有效但记录不存在 -> 404
    missing_token = info_tokens.make_token("missing")
    assert client.get(f"/api/admin/info/media/missing?token={missing_token}").status_code == 404

    token = info_tokens.make_token(media_id)
    ok = client.get(f"/api/admin/info/media/{media_id}?token={token}")
    assert ok.status_code == 200
    assert ok.content == payload
    assert ok.headers["content-type"] == "image/jpeg"
    assert "nosniff" in ok.headers.get("x-content-type-options", "")

    # 令牌绑定 media_id：换一个 id 不通过
    assert info_tokens.verify_token("other-media", token) is False


def _make_expired_token(media_id: str) -> str:
    """手搓一个已过期令牌。

    `make_token` 会把 TTL 下限夹到 1 秒（避免误生成"出生即过期"的地址），所以这里
    直接用同一套算法构造过期时间在过去的令牌。
    """
    payload = f"{info_tokens._b64e(media_id.encode())}.{int(time.time()) - 10}"
    return f"{payload}.{info_tokens._sign(payload)}"


def test_media_token_expiry(client: TestClient) -> None:
    media_id, _ = _seed_ready_media(client)
    expired = _make_expired_token(media_id)
    assert info_tokens.media_id_from_token(expired) is None
    assert client.get(f"/api/admin/info/media/{media_id}?token={expired}").status_code == 401


def test_purged_media_returns_410(client: TestClient) -> None:
    media_id, _ = _seed_ready_media(client, purged=1)
    token = info_tokens.make_token(media_id)
    assert client.get(f"/api/admin/info/media/{media_id}?token={token}").status_code == 410


# ---------------------------------------------------------------- 保留清理


def test_retention_purges_old_media_and_keeps_metadata(client: TestClient, monkeypatch) -> None:
    from app.config import get_settings, reset_settings
    from app.db import get_session_factory
    from app.models import InfoItem, InfoMedia
    from app.services.info_retention import cleanup_once

    media_id, _ = _seed_ready_media(client)

    session = get_session_factory()()
    try:
        row = session.get(InfoMedia, media_id)
        assert row is not None
        row.created_at = datetime(2020, 1, 1)
        session.commit()
    finally:
        session.close()

    monkeypatch.setenv("INFO_RETENTION_DAYS", "7")
    reset_settings()
    try:
        assert get_settings().info_retention_days == 7
        result = cleanup_once()
    finally:
        reset_settings()

    assert result["media"] == 1

    session = get_session_factory()()
    try:
        row = session.get(InfoMedia, media_id)
        assert row is not None and row.purged == 1
        # 元数据保留，只有文件被清掉
        assert session.get(InfoItem, "item-1") is not None
    finally:
        session.close()

    assert not storage.media_path("item-1", "000.jpg").exists()

    # 文件被清理后即使令牌有效也返回 410
    token = info_tokens.make_token(media_id)
    assert client.get(f"/api/admin/info/media/{media_id}?token={token}").status_code == 410


def test_stats_reports_counts(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: FakeAdapter([_demo_page()]))
    source_id = _create_source(client, auth_headers)
    client.post(f"/api/admin/info/sources/{source_id}/collect", headers=auth_headers)

    stats = client.get("/api/admin/info/stats", headers=auth_headers).json()
    assert stats["source_count"] == 1
    assert stats["item_count"] == 3
    assert stats["media_bytes"] == 0
    assert stats["last_collect_at"] is not None
    assert isinstance(stats["provider_configured"], bool)


# ---------------------------------------------------------------- 采集间隔：手动与批量


def test_manual_interval_value_and_scheduling(client: TestClient) -> None:
    from app.db import get_session_factory
    from app.info.sources import clamp_interval, due_sources
    from app.models import InfoSource

    # 0 / 负数 = 手动触发；None 用默认自动间隔
    assert clamp_interval(0) == 0
    assert clamp_interval(-5) == 0
    assert clamp_interval(None) == 86400

    now = utcnow()
    session = get_session_factory()()
    try:
        session.add_all(
            [
                InfoSource(
                    id="s-manual", kind="telegram", identifier="manualch", title="manual",
                    username="manualch", description="", avatar_url="", subscriber_count_text="",
                    enabled=True, poll_interval_seconds=0, item_count=0,
                    created_at=now, updated_at=now,
                ),
                InfoSource(
                    id="s-auto", kind="telegram", identifier="autoch", title="auto",
                    username="autoch", description="", avatar_url="", subscriber_count_text="",
                    enabled=True, poll_interval_seconds=86400, last_polled_at=None,
                    item_count=0, created_at=now, updated_at=now,
                ),
            ]
        )
        session.commit()
        due = {source.id for source in due_sources(session)}
        assert "s-manual" not in due  # 手动触发不参与定时轮询
        assert "s-auto" in due
    finally:
        session.close()


def test_batch_update_interval_endpoint(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    monkeypatch.setattr("app.info.collector.adapter_for", lambda db, kind: FakeAdapter([]))
    first = _create_source(client, auth_headers, "@ch1")
    second = _create_source(client, auth_headers, "@ch2")

    # 指定子集，设为手动触发
    response = client.post(
        "/api/admin/info/sources/batch-interval",
        headers=auth_headers,
        json={"ids": [first], "poll_interval_seconds": 0},
    )
    assert response.status_code == 200, response.text
    assert response.json()["updated"] == 1

    listed = {
        row["id"]: row
        for row in client.get("/api/admin/info/sources", headers=auth_headers).json()["sources"]
    }
    assert listed[first]["poll_interval_seconds"] == 0
    assert listed[second]["poll_interval_seconds"] == 86400

    # 不传 ids = 全部渠道
    response = client.post(
        "/api/admin/info/sources/batch-interval",
        headers=auth_headers,
        json={"poll_interval_seconds": 3600},
    )
    assert response.json()["updated"] == 2
    listed = {
        row["id"]: row
        for row in client.get("/api/admin/info/sources", headers=auth_headers).json()["sources"]
    }
    assert all(row["poll_interval_seconds"] == 3600 for row in listed.values())

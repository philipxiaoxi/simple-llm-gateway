from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.capabilities.docparse.convert import ConvertResult
from app.capabilities.web import provider as web_provider
from app.capabilities.web.fetch import FetchResult

_PARAGRAPHS = "".join(
    f"<p>这是第{i}段正文，讲的是易微联开门流程与网关限流策略，用来凑够正文长度。</p>" for i in range(1, 10)
)
ARTICLE = f"""<!doctype html><html><head><title>测试文章</title>
<meta name="author" content="张三">
<meta property="article:published_time" content="2026-10-01T08:00:00Z">
</head><body>
<nav><a href="/">首页</a><a href="/about">关于</a></nav>
<article><h1>测试文章</h1>
{_PARAGRAPHS}
</article>
<footer>版权所有 2026</footer></body></html>"""


def make_key(client: TestClient, auth_headers: dict[str, str], capabilities: list[str]) -> str:
    created = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "web-key", "capability_ids": capabilities},
    )
    assert created.status_code == 201, created.text
    return created.json()["key"]


@pytest.fixture(autouse=True)
def _clear_web_cache() -> None:
    web_provider.clear_cache()


def _fake_fetch(result: FetchResult):
    async def _inner(*_args, **_kwargs):
        return result

    return _inner


def _allow_robots(monkeypatch) -> None:
    async def _allowed(_url: str) -> bool:
        return True

    monkeypatch.setattr("app.capabilities.web.robots.allowed", _allowed)


def test_web_fetch_rejects_non_http(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = make_key(client, auth_headers, ["web"])
    response = client.post(
        "/v1/capabilities/web/fetch",
        headers={"Authorization": f"Bearer {key}"},
        json={"url": "ftp://example.com/file"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "blocked_host"


def test_web_fetch_rejects_private_host(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = make_key(client, auth_headers, ["web"])
    response = client.post(
        "/v1/capabilities/web/fetch",
        headers={"Authorization": f"Bearer {key}"},
        json={"url": "http://127.0.0.1/secret"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "blocked_host"


def test_web_fetch_extracts_markdown(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    _allow_robots(monkeypatch)
    result = FetchResult(
        url="https://example.com/a",
        final_url="https://example.com/a",
        status=200,
        content_type="text/html",
        body=ARTICLE.encode("utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.capabilities.web.provider.fetch", _fake_fetch(result))
    key = make_key(client, auth_headers, ["web"])

    response = client.post(
        "/v1/capabilities/web/fetch",
        headers={"Authorization": f"Bearer {key}"},
        json={"url": "https://example.com/a"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "测试文章"
    assert body["published_at"] == "2026-10-01"
    assert "易微联开门流程" in body["markdown"]
    assert "首页" not in body["markdown"]
    assert "版权所有" not in body["markdown"]
    assert body["truncated"] is False
    assert body["extract_fallback"] is False


def test_web_fetch_truncates(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    _allow_robots(monkeypatch)
    long_body = "".join(
        f"<p>第{i}段正文，讲的是易微联开门流程与网关限流策略，用来凑够正文长度。</p>" for i in range(1, 80)
    )
    long_html = f"<html><head><title>长文</title></head><body><article>{long_body}</article></body></html>"
    result = FetchResult(
        url="https://example.com/a",
        final_url="https://example.com/a",
        status=200,
        content_type="text/html",
        body=long_html.encode("utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.capabilities.web.provider.fetch", _fake_fetch(result))
    key = make_key(client, auth_headers, ["web"])

    response = client.post(
        "/v1/capabilities/web/fetch",
        headers={"Authorization": f"Bearer {key}"},
        json={"url": "https://example.com/a", "max_chars": 1000},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["truncated"] is True
    assert body["chars"] == 1000


def test_web_fetch_routes_pdf_to_docparse(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    _allow_robots(monkeypatch)
    result = FetchResult(
        url="https://example.com/a.pdf",
        final_url="https://example.com/a.pdf",
        status=200,
        content_type="application/pdf",
        body=b"%PDF-1.4 fake",
    )
    monkeypatch.setattr("app.capabilities.web.provider.fetch", _fake_fetch(result))
    monkeypatch.setattr(
        "app.capabilities.web.provider.convert_bytes",
        lambda name, raw: ConvertResult(markdown="# PDF 正文", page_count=1),
    )
    key = make_key(client, auth_headers, ["web"])

    response = client.post(
        "/v1/capabilities/web/fetch",
        headers={"Authorization": f"Bearer {key}"},
        json={"url": "https://example.com/a.pdf"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["markdown"] == "# PDF 正文"


def test_web_fetch_respects_robots(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    async def _disallowed(_url: str) -> bool:
        return False

    monkeypatch.setattr("app.capabilities.web.robots.allowed", _disallowed)
    key = make_key(client, auth_headers, ["web"])

    response = client.post(
        "/v1/capabilities/web/fetch",
        headers={"Authorization": f"Bearer {key}"},
        json={"url": "https://example.com/a"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "robots_disallowed"


def test_web_fetch_caches_result(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    _allow_robots(monkeypatch)
    calls = {"count": 0}
    result = FetchResult(
        url="https://example.com/a",
        final_url="https://example.com/a",
        status=200,
        content_type="text/html",
        body=ARTICLE.encode("utf-8"),
        encoding="utf-8",
    )

    async def _counting(*_args, **_kwargs):
        calls["count"] += 1
        return result

    monkeypatch.setattr("app.capabilities.web.provider.fetch", _counting)
    key = make_key(client, auth_headers, ["web"])

    for _ in range(2):
        response = client.post(
            "/v1/capabilities/web/fetch",
            headers={"Authorization": f"Bearer {key}"},
            json={"url": "https://example.com/a"},
        )
        assert response.status_code == 200, response.text
    assert calls["count"] == 1


def test_web_capability_in_catalog(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = make_key(client, auth_headers, ["web"])
    response = client.get("/v1/capabilities", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200, response.text
    ids = [item["capability_id"] for item in response.json()]
    assert "web" in ids


def _add_item(text: str, *, permalink: str = "https://example.com/post", author: str = "作者") -> str:
    from app.db import get_session_factory
    from app.info import search_index
    from app.models import InfoItem

    session = get_session_factory()()
    try:
        item = InfoItem(
            id=f"item-{abs(hash(text)) % 10**8}",
            source_id=None,
            external_id=str(abs(hash(text)) % 10**8),
            kind="text",
            text=text,
            excerpt=text[:200],
            permalink=permalink,
            author_name=author,
            source_type="test",
            status="ready",
        )
        session.add(item)
        session.flush()
        search_index.index_item(session, item)
        session.commit()
        return item.id
    finally:
        session.close()


def test_web_search_matches_two_char_chinese(client: TestClient, auth_headers: dict[str, str]) -> None:
    _add_item("易微联开门流程图与网关限流策略说明")
    key = make_key(client, auth_headers, ["web"])
    response = client.post(
        "/v1/capabilities/web/search",
        headers={"Authorization": f"Bearer {key}"},
        json={"query": "开门", "scope": "local"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["results"], body
    assert "易微联开门流程图" in body["results"][0]["snippet"]


def test_web_search_reindexes_existing_rows(client: TestClient, auth_headers: dict[str, str]) -> None:
    from app.db import get_session_factory
    from app.models import InfoItem

    session = get_session_factory()()
    try:
        session.add(
            InfoItem(
                id="item-reindex",
                source_id=None,
                external_id="reindex",
                kind="text",
                text="网关限流策略与单飞锁",
                excerpt="网关限流策略与单飞锁",
                permalink="https://example.com/reindex",
                source_type="test",
                status="ready",
            )
        )
        session.commit()
    finally:
        session.close()

    key = make_key(client, auth_headers, ["web"])
    response = client.post(
        "/v1/capabilities/web/search",
        headers={"Authorization": f"Bearer {key}"},
        json={"query": "限流", "scope": "local"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"]


def test_web_search_web_scope_without_provider(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = make_key(client, auth_headers, ["web"])
    response = client.post(
        "/v1/capabilities/web/search",
        headers={"Authorization": f"Bearer {key}"},
        json={"query": "anything", "scope": "web"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["results"] == []
    assert body["warnings"]


def test_web_search_merges_external_provider(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    from app.capabilities.web.providers.base import SearchResult

    class FakeProvider:
        name = "searxng"

        async def search(self, query: str, *, top_k: int, time_range: str, site: str):
            return [
                SearchResult(
                    title="外部结果",
                    url="https://external.example.com/page",
                    snippet="外部摘要",
                    provider="searxng",
                )
            ]

    monkeypatch.setattr("app.capabilities.web.search.resolve_provider", lambda: FakeProvider())
    key = make_key(client, auth_headers, ["web"])
    response = client.post(
        "/v1/capabilities/web/search",
        headers={"Authorization": f"Bearer {key}"},
        json={"query": "外部", "scope": "web"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["results"][0]["url"] == "https://external.example.com/page"
    assert body["results"][0]["provider"] == "searxng"
    assert body["warnings"] == []

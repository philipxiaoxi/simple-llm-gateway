from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from app.capabilities.douyin.errors import DouyinError
from app.capabilities.douyin.extractor.base import ExtractedMedia, ExtractedWork
from app.capabilities.douyin.resolver import extract_url, resolve_target

SAMPLE_SHARE = (
    "5.84 复制打开抖音，看看【AgonyLeii的作品】高速服务区充电排队的最根本原因 "
    "https://v.douyin.com/K6QsRUZrYJ4/ 12/04 Xmq:/ :8pm L@j.cN"
)


def make_key(client: TestClient, auth_headers: dict[str, str], capabilities: list[str]) -> str:
    created = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "douyin-key", "capability_ids": capabilities},
    )
    assert created.status_code == 201, created.text
    return created.json()["key"]


def fake_extractor(media: list[ExtractedMedia], *, kind: str = "video"):
    class _Extractor:
        name = "fake"

        def available(self) -> bool:
            return True

        def extract(self, target):  # noqa: ANN001
            return ExtractedWork(
                extractor="fake",
                aweme_id="1234567890",
                title="测试作品",
                author_name="作者",
                author_id="author-1",
                cover_url="https://p3.douyinpic.com/cover.jpeg",
                duration_ms=15000,
                kind=kind,
                media=media,
            )

    extractor = _Extractor()
    return lambda *args, **kwargs: extractor


def install_downloader(monkeypatch, *, fail_urls: set[str] | None = None) -> None:
    fail_urls = fail_urls or set()

    def fake_fetch(url: str, dest: Path, *, max_bytes: int, kind: str, on_progress=None):
        if url in fail_urls:
            raise DouyinError("下载失败", status_code=422, error_type="download_failed")
        data = b"DATA:" + url.encode("utf-8")
        Path(dest).write_bytes(data)
        content_type = {"video": "video/mp4", "image": "image/jpeg", "audio": "audio/mpeg"}[kind]
        if on_progress:
            on_progress(0, len(data))
            on_progress(len(data), len(data))
        return len(data), content_type, "sha-" + str(len(url))

    monkeypatch.setattr("app.capabilities.douyin.downloader.fetch_to_file", fake_fetch)


def admin_create(client: TestClient, auth_headers: dict[str, str], body: dict):
    return client.post("/api/admin/mcp/douyin/jobs", headers=auth_headers, json=body)


# ---- resolver ----


def test_extract_url_from_share_text() -> None:
    assert extract_url(SAMPLE_SHARE) == "https://v.douyin.com/K6QsRUZrYJ4/"
    assert extract_url("没有链接的文本") is None


def test_resolve_target_rejects_non_douyin_host() -> None:
    with pytest.raises(DouyinError) as error:
        resolve_target("https://evil.com/video/123")
    assert error.value.error_type == "blocked_host"


def test_resolve_target_rejects_private_ip() -> None:
    with pytest.raises(DouyinError):
        resolve_target("https://v.douyin.com.evil.com/xx")
    # 直接指向私有地址的 URL 一律拒绝
    from app.capabilities.douyin.resolver import assert_safe_url

    with pytest.raises(DouyinError):
        assert_safe_url("http://127.0.0.1/video.mp4")


# ---- admin end-to-end ----


def test_admin_parse_download_and_serve(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.capabilities.douyin.jobs.build_extractor",
        fake_extractor([ExtractedMedia(kind="video", url="https://v3.douyinvod.com/v.mp4")]),
    )
    install_downloader(monkeypatch)

    response = admin_create(client, auth_headers, {"share_text": SAMPLE_SHARE})
    assert response.status_code == 202, response.text
    submitted = response.json()
    assert submitted["status"] == "queued"
    assert submitted["stage"] == "resolving"
    assert submitted["downloaded_bytes"] == 0
    job_id = submitted["id"]

    detail = client.get(f"/api/admin/mcp/douyin/jobs/{job_id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "succeeded", body
    assert body["extractor"] == "fake"
    assert body["media_count"] == 1
    assert body["downloaded_bytes"] == body["total_bytes"] > 0
    assert body["expected_bytes"] >= body["downloaded_bytes"]
    media = body["media"][0]
    assert media["kind"] == "video"
    assert media["status"] == "ready"
    assert media["download_url"].startswith("/v1/douyin/media/")
    assert media["absolute_download_url"].startswith("http://testserver/v1/douyin/media/")

    parsed = urlparse(media["download_url"])
    download = client.get(parsed.path + "?" + parsed.query)
    assert download.status_code == 200, download.text
    assert download.content == b"DATA:https://v3.douyinvod.com/v.mp4"
    assert download.headers["content-type"].startswith("video/mp4")
    assert download.headers["x-content-type-options"] == "nosniff"

    no_token = client.get(parsed.path)
    assert no_token.status_code == 401


def test_public_share_meta(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.capabilities.douyin.jobs.build_extractor",
        fake_extractor([ExtractedMedia(kind="video", url="https://v3.douyinvod.com/share.mp4")]),
    )
    install_downloader(monkeypatch)

    created = admin_create(client, auth_headers, {"share_text": SAMPLE_SHARE})
    job_id = created.json()["id"]
    detail = client.get(f"/api/admin/mcp/douyin/jobs/{job_id}", headers=auth_headers).json()
    media = detail["media"][0]
    token = media["token"]
    assert token

    # 公开接口无需登录，且令牌自包含 media_id
    from app.capabilities.douyin.tokens import media_id_from_token

    assert media_id_from_token(token) == media["id"]

    meta = client.get(f"/v1/douyin/share?token={token}")
    assert meta.status_code == 200, meta.text
    body = meta.json()
    assert body["id"] == media["id"]
    assert body["kind"] == "video"
    assert body["title"] == "测试作品"
    assert body["download_url"].startswith(f"/v1/douyin/media/{media['id']}")

    assert client.get("/v1/douyin/share?token=bad").status_code == 401
    assert client.get("/v1/douyin/share").status_code == 401


def test_admin_parse_gallery(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    media = [
        ExtractedMedia(kind="image", url="https://p3.douyinpic.com/1.jpeg"),
        ExtractedMedia(kind="image", url="https://p3.douyinpic.com/2.jpeg"),
        ExtractedMedia(kind="image", url="https://p3.douyinpic.com/3.jpeg"),
    ]
    monkeypatch.setattr("app.capabilities.douyin.jobs.build_extractor", fake_extractor(media, kind="gallery"))
    install_downloader(monkeypatch)

    response = admin_create(client, auth_headers, {"url": "https://www.douyin.com/note/987654321"})
    assert response.status_code == 202, response.text
    detail = client.get(
        f"/api/admin/mcp/douyin/jobs/{response.json()['id']}", headers=auth_headers
    ).json()
    assert detail["status"] == "succeeded"
    assert [item["index_no"] for item in detail["media"]] == [1, 2, 3]
    assert all(item["kind"] == "image" for item in detail["media"])


def test_partial_failure_and_retry(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    media = [
        ExtractedMedia(kind="image", url="https://p3.douyinpic.com/ok.jpeg"),
        ExtractedMedia(kind="image", url="https://p3.douyinpic.com/bad.jpeg"),
    ]
    monkeypatch.setattr("app.capabilities.douyin.jobs.build_extractor", fake_extractor(media, kind="gallery"))
    install_downloader(monkeypatch, fail_urls={"https://p3.douyinpic.com/bad.jpeg"})

    response = admin_create(client, auth_headers, {"url": "https://www.douyin.com/note/111"})
    job_id = response.json()["id"]
    detail = client.get(f"/api/admin/mcp/douyin/jobs/{job_id}", headers=auth_headers).json()
    assert detail["status"] == "partial"
    assert detail["success_count"] == 1

    # 重试时不再失败
    install_downloader(monkeypatch)
    retry = client.post(f"/api/admin/mcp/douyin/jobs/{job_id}/retry", headers=auth_headers)
    assert retry.status_code == 202, retry.text
    detail = client.get(f"/api/admin/mcp/douyin/jobs/{job_id}", headers=auth_headers).json()
    assert detail["status"] == "succeeded"
    assert detail["success_count"] == 2


def test_rehost_false_skips_download(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.capabilities.douyin.jobs.build_extractor",
        fake_extractor([ExtractedMedia(kind="video", url="https://v3.douyinvod.com/v.mp4")]),
    )

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("rehost=false 不应触发下载")

    monkeypatch.setattr("app.capabilities.douyin.downloader.fetch_to_file", boom)
    response = admin_create(
        client, auth_headers, {"url": "https://www.douyin.com/video/222", "rehost": False}
    )
    assert response.status_code == 202, response.text
    detail = client.get(
        f"/api/admin/mcp/douyin/jobs/{response.json()['id']}", headers=auth_headers
    ).json()
    assert detail["status"] == "succeeded"
    media = detail["media"][0]
    assert media["status"] == "ready"
    assert media["original_url"] == "https://v3.douyinvod.com/v.mp4"
    assert media["download_url"] is None


def test_parse_rejects_bad_host(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = admin_create(client, auth_headers, {"share_text": "http://127.0.0.1/secret"})
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["type"] == "blocked_host"


# ---- protocol surface ----


def test_protocol_requires_douyin_capability(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    key = make_key(client, auth_headers, ["knowledge"])
    response = client.post(
        "/v1/douyin/parse",
        headers={"Authorization": f"Bearer {key}"},
        json={"url": "https://www.douyin.com/video/333"},
    )
    assert response.status_code == 403, response.text


def test_protocol_parse_inline_and_list(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    key = make_key(client, auth_headers, ["douyin"])
    monkeypatch.setattr(
        "app.capabilities.douyin.jobs.build_extractor",
        fake_extractor([ExtractedMedia(kind="video", url="https://v3.douyinvod.com/proto.mp4")]),
    )
    install_downloader(monkeypatch)

    response = client.post(
        "/v1/douyin/parse",
        headers={"Authorization": f"Bearer {key}"},
        json={"url": "https://www.douyin.com/video/444"},
    )
    assert response.status_code == 202, response.text
    detail = client.get(
        f"/v1/douyin/jobs/{response.json()['id']}", headers={"Authorization": f"Bearer {key}"}
    ).json()
    assert detail["status"] == "succeeded"

    listed = client.get("/v1/douyin/jobs", headers={"Authorization": f"Bearer {key}"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    # 通用能力分发入口同样可用（MCP 工具走的就是这条）
    generic = client.post(
        "/v1/capabilities/douyin/list",
        headers={"Authorization": f"Bearer {key}"},
        json={},
    )
    assert generic.status_code == 200, generic.text
    assert len(generic.json()["items"]) == 1


def test_protocol_sk_key_is_unauthorized(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/v1/douyin/jobs", headers={"Authorization": "Bearer sk-nope"})
    assert response.status_code == 401


def test_tikhub_provider_config(client: TestClient, auth_headers: dict[str, str]) -> None:
    initial = client.get("/api/admin/mcp/douyin/provider", headers=auth_headers).json()
    assert initial["configured"] is False

    saved = client.put(
        "/api/admin/mcp/douyin/provider",
        headers=auth_headers,
        json={"base_url": "https://api.tikhub.io", "api_key": "tk_secret"},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["configured"] is True
    assert body["has_key"] is True
    assert body["source"] == "page"
    assert "tk_secret" not in saved.text

    cleared = client.delete("/api/admin/mcp/douyin/provider", headers=auth_headers).json()
    assert cleared["configured"] is False


def test_tikhub_extractor_maps_response(monkeypatch) -> None:
    from app.capabilities.douyin.extractor.tikhub import TikHubExtractor
    from app.capabilities.douyin.resolver import Target

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):  # noqa: ANN201
            return {
                "success": True,
                "data": {
                    "aweme_detail": {
                        "aweme_id": "1234567890",
                        "desc": "标题",
                        "author": {"nickname": "作者", "uid": "u1"},
                        "video": {
                            "duration": 15000,
                            "play_addr": {"url_list": ["https://v3.douyinvod.com/x.mp4"]},
                            "cover": {"url_list": ["https://p3.douyinpic.com/c.jpeg"]},
                        },
                    }
                },
            }

    captured: dict = {}

    def fake_get(url, params=None, headers=None, timeout=None, follow_redirects=True):  # noqa: ANN001
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)
    extractor = TikHubExtractor(api_key="tk_test", base_url="https://api.tikhub.io")
    work = extractor.extract(Target(url="https://v.douyin.com/abc/"))
    assert work.extractor == "tikhub"
    assert work.aweme_id == "1234567890"
    assert work.title == "标题"
    assert work.author_name == "作者"
    assert work.media[0].kind == "video"
    assert captured["url"].endswith("/api/v1/hybrid/video_data")
    assert captured["params"] == {"url": "https://v.douyin.com/abc/"}
    assert captured["headers"]["Authorization"] == "Bearer tk_test"


def test_aweme_builds_gallery_from_image_post_info() -> None:
    from app.capabilities.douyin.extractor.aweme import build_work

    aweme = {
        "aweme_id": "7gallery",
        "desc": "图集标题",
        "author": {"nickname": "作者", "uid": "u2"},
        "image_post_info": {
            "images": [
                {"url_list": ["https://p3.douyinpic.com/a.jpeg"], "width": 1080, "height": 1440},
                {"url_list": ["https://p3.douyinpic.com/b.jpeg"], "width": 1080, "height": 1440},
            ]
        },
        "music": {"play_url": {"url_list": ["https://sf.douyin.com/music.mp3"]}},
    }
    work = build_work(aweme, "tikhub")
    assert work.kind == "gallery"
    assert [m.kind for m in work.media] == ["image", "image", "audio"]
    assert work.media[0].url == "https://p3.douyinpic.com/a.jpeg"
    assert work.media[0].width == 1080


def test_concurrent_limit(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    from app.config import get_settings

    key = make_key(client, auth_headers, ["douyin"])
    headers = {"Authorization": f"Bearer {key}"}
    real = get_settings()
    monkeypatch.setattr(
        "app.capabilities.douyin.jobs.get_settings",
        lambda: real.model_copy(update={"douyin_max_concurrent_per_key": 1}),
    )
    monkeypatch.setattr(
        "app.capabilities.douyin.jobs.build_extractor",
        fake_extractor([ExtractedMedia(kind="video", url="https://v3.douyinvod.com/v.mp4")]),
    )
    # 后台不推进转存，任务保持 queued，触发并发计数
    monkeypatch.setattr("app.capabilities.douyin.jobs.run_job_task", lambda job_id: None)

    first = client.post(
        "/v1/douyin/parse", headers=headers, json={"url": "https://www.douyin.com/video/900"}
    )
    assert first.status_code == 202, first.text
    second = client.post(
        "/v1/douyin/parse", headers=headers, json={"url": "https://www.douyin.com/video/901"}
    )
    assert second.status_code == 429, second.text


def test_mcp_parse_respects_wait_deadline(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    import time as time_module

    key = make_key(client, auth_headers, ["douyin"])
    monkeypatch.setattr(
        "app.capabilities.douyin.jobs.build_extractor",
        fake_extractor([ExtractedMedia(kind="video", url="https://v3.douyinvod.com/big.mp4")]),
    )

    def slow_fetch(url, dest, *, max_bytes, kind, on_progress=None):  # noqa: ANN001
        time_module.sleep(1.3)
        if on_progress:
            on_progress(1, 100)  # 超过 wait 窗口后的进度回调应中断下载
        return 1, "video/mp4", "sha"

    monkeypatch.setattr("app.capabilities.douyin.downloader.fetch_to_file", slow_fetch)

    response = client.post(
        "/v1/capabilities/douyin/parse",
        headers={"Authorization": f"Bearer {key}"},
        json={"url": "https://www.douyin.com/video/777", "wait_seconds": 1},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "downloading", body
    assert body["media"][0]["status"] == "pending", body


def test_catalog_lists_douyin(client: TestClient, auth_headers: dict[str, str]) -> None:
    catalog = client.get("/api/admin/mcp/catalog", headers=auth_headers)
    assert catalog.status_code == 200
    ids = {item["capability_id"] for item in catalog.json()["items"]}
    assert "douyin" in ids




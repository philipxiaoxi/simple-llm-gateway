from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.staticfiles import StaticFiles


def test_static_directory_stays_inside_root(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("ok")
    (tmp_path / ".env").write_text("SECRET")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "gateway.db").write_text("db")
    static = StaticFiles(directory=assets)
    escaped_env = static.lookup_path("../../.env")
    escaped_db = static.lookup_path("../data/gateway.db")
    assert escaped_env == ("", None)
    assert escaped_db == ("", None)


def test_spa_unknown_path_returns_index(client: TestClient) -> None:
    from app.main import FRONTEND_DIST

    if not FRONTEND_DIST.exists():
        pytest.skip("frontend dist 不存在")
    response = client.get("/accounts")
    assert response.status_code == 200
    assert "AI一体化服务平台" in response.text
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/", "/sw.js", "/manifest.webmanifest"])
def test_pwa_update_files_do_not_use_stale_cache(client: TestClient, path: str) -> None:
    from app.main import FRONTEND_DIST

    if not FRONTEND_DIST.exists():
        pytest.skip("frontend dist 不存在")
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_workbox_runtime_file_does_not_use_stale_cache(client: TestClient) -> None:
    from app.main import FRONTEND_DIST

    if not FRONTEND_DIST.exists():
        pytest.skip("frontend dist 不存在")
    workbox_files = sorted(FRONTEND_DIST.glob("workbox-*.js"))
    if not workbox_files:
        pytest.skip("workbox 运行时文件不存在")
    response = client.get(f"/{workbox_files[0].name}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_spa_does_not_serve_repo_files(client: TestClient) -> None:
    from app.main import FRONTEND_DIST

    if not FRONTEND_DIST.exists():
        pytest.skip("frontend dist 不存在")
    response = client.get("/.env")
    assert response.status_code == 200
    assert "APP_SECRET_KEY" not in response.text
    assert "AI一体化服务平台" in response.text


def test_spa_does_not_swallow_unknown_api_posts(client: TestClient) -> None:
    response = client.post("/api/does-not-exist")
    assert response.status_code == 404
    assert "Method Not Allowed" not in response.text
    assert response.headers.get("cache-control") == "no-store"


def test_missing_static_file_returns_404_not_500(client: TestClient) -> None:
    """dist 里没有的文件（例如已下线的图标）应报 404，不能抛 500。"""
    from app.main import FRONTEND_DIST

    if not FRONTEND_DIST.exists():
        pytest.skip("frontend dist 不存在")
    response = client.get("/icons.svg")
    assert response.status_code in {200, 404}
    assert response.status_code != 500


@pytest.mark.parametrize("path", ["/favicon.svg", "/sw.js", "/manifest.webmanifest"])
def test_head_matches_get_for_static_files(client: TestClient, path: str) -> None:
    """HEAD 必须返回该路径自己的元数据，不能落到 SPA 的 index.html。"""
    from app.main import FRONTEND_DIST

    if not FRONTEND_DIST.exists():
        pytest.skip("frontend dist 不存在")
    get_response = client.get(path)
    head_response = client.head(path)
    assert head_response.status_code == get_response.status_code
    assert head_response.headers["content-type"] == get_response.headers["content-type"]
    assert head_response.headers["cache-control"] == get_response.headers["cache-control"]
    # 文本响应经过 gzip 后 GET 不带 content-length，只有未压缩时才要求一致
    if get_response.status_code == 200 and "content-length" in get_response.headers:
        assert head_response.headers["content-length"] == get_response.headers["content-length"]


def test_self_hosted_fonts_are_served_as_fonts(client: TestClient) -> None:
    """字体必须由 /fonts 直接托管；落到 SPA 兜底会拿到 HTML，浏览器静默回退系统字体。"""
    from app.main import FRONTEND_DIST

    font_dir = FRONTEND_DIST / "fonts"
    if not font_dir.is_dir() or not any(font_dir.glob("*.woff2")):
        pytest.skip("未自托管字体")
    name = sorted(font_dir.glob("*.woff2"))[0].name
    response = client.get(f"/fonts/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "font/woff2"
    assert response.headers["cache-control"].startswith("public, max-age=")
    assert response.content[:4] == b"wOF2"


def test_unknown_font_path_is_not_spa_fallback(client: TestClient) -> None:
    from app.main import FRONTEND_DIST

    if not (FRONTEND_DIST / "fonts").is_dir():
        pytest.skip("未自托管字体")
    response = client.get("/fonts/does-not-exist.woff2")
    assert response.status_code == 404


def test_voice_worklet_is_served_as_javascript(client: TestClient) -> None:
    """AudioWorklet 必须直接拿到 JS。

    曾被 SPA 兜底成 index.html，导致 audioWorklet.addModule() 抛 AbortError，
    前端静默降级到 ScriptProcessorNode——功能还在，但「边说边出字」的延迟明显变差，
    而且不会有任何报错提示，很难被发现。
    """
    from app.main import FRONTEND_DIST

    worklet = FRONTEND_DIST / "voice-worklet.js"
    if not worklet.is_file():
        pytest.skip("前端未构建，没有 voice-worklet.js")

    response = client.get("/voice-worklet.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert response.headers["cache-control"] == "no-cache"
    assert b"registerProcessor" in response.content
    assert not response.content.lstrip().startswith(b"<!doctype html")

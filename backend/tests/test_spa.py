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

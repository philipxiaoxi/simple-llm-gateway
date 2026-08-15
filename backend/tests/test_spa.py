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
    assert "中转台" in response.text


def test_spa_does_not_serve_repo_files(client: TestClient) -> None:
    from app.main import FRONTEND_DIST

    if not FRONTEND_DIST.exists():
        pytest.skip("frontend dist 不存在")
    response = client.get("/.env")
    assert response.status_code == 200
    assert "APP_SECRET_KEY" not in response.text
    assert "中转台" in response.text

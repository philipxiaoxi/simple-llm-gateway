from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("APP_SECRET_KEY", "unit-test-secret-key")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin123")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "gateway.db"))
    monkeypatch.setenv("APP_BASE_URL", "http://testserver")

    from app.config import reset_settings
    from app.db import reset_db_runtime
    from app.login_gate import login_gate

    reset_settings()
    reset_db_runtime()
    login_gate.reset()

    from app.main import app
    from app.db import init_db
    from app.seed import seed_admin

    init_db()
    seed_admin()
    with TestClient(app) as test_client:
        yield test_client

    reset_db_runtime()
    reset_settings()


@pytest.fixture()
def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/admin/login", json={"username": "admin", "password": "admin123"})
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}

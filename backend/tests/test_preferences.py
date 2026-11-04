from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_permissions import PASSWORD, _create_role, _create_user, _login


def test_theme_defaults_to_system(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/admin/me/preferences", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"theme": "system"}


def test_theme_saved_and_returned(client: TestClient, auth_headers: dict[str, str]) -> None:
    saved = client.put("/api/admin/me/preferences", headers=auth_headers, json={"theme": "light"})
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"theme": "light"}

    fetched = client.get("/api/admin/me/preferences", headers=auth_headers)
    assert fetched.json() == {"theme": "light"}

    # 同账号再次登录后仍然是保存的值
    token = client.post("/api/admin/login", json={"username": "admin", "password": "admin123"}).json()["token"]
    again = client.get("/api/admin/me/preferences", headers={"Authorization": f"Bearer {token}"})
    assert again.json() == {"theme": "light"}

    updated = client.put("/api/admin/me/preferences", headers=auth_headers, json={"theme": "dark"})
    assert updated.json() == {"theme": "dark"}


def test_theme_normalized_and_rejects_unknown(client: TestClient, auth_headers: dict[str, str]) -> None:
    upper = client.put("/api/admin/me/preferences", headers=auth_headers, json={"theme": " LIGHT "})
    assert upper.status_code == 200, upper.text
    assert upper.json() == {"theme": "light"}

    bad = client.put("/api/admin/me/preferences", headers=auth_headers, json={"theme": "auto"})
    assert bad.status_code == 422

    missing = client.put("/api/admin/me/preferences", headers=auth_headers, json={})
    assert missing.status_code == 422


def test_theme_is_per_account(client: TestClient, auth_headers: dict[str, str]) -> None:
    client.put("/api/admin/me/preferences", headers=auth_headers, json={"theme": "dark"})

    role = _create_role(client, auth_headers, "只读概览", ["dashboard:view"])
    _create_user(client, auth_headers, "theme-reader", role["id"])
    reader = _login(client, "theme-reader", PASSWORD)

    assert client.get("/api/admin/me/preferences", headers=reader).json() == {"theme": "system"}

    reader_saved = client.put("/api/admin/me/preferences", headers=reader, json={"theme": "light"})
    assert reader_saved.status_code == 200, reader_saved.text
    assert client.get("/api/admin/me/preferences", headers=auth_headers).json() == {"theme": "dark"}

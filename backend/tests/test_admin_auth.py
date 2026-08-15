from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_success(client: TestClient) -> None:
    response = client.post("/api/admin/login", json={"username": "admin", "password": "admin123"})
    assert response.status_code == 200
    assert response.json()["username"] == "admin"
    assert response.json()["token"]


def test_login_wrong_password(client: TestClient) -> None:
    response = client.post("/api/admin/login", json={"username": "admin", "password": "nope"})
    assert response.status_code == 401


def test_me_requires_token(client: TestClient) -> None:
    response = client.get("/api/admin/me")
    assert response.status_code == 401


def test_me_with_token(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/admin/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["username"] == "admin"


def test_update_admin_password(client: TestClient, auth_headers: dict[str, str]) -> None:
    changed = client.patch(
        "/api/admin/me",
        headers=auth_headers,
        json={"current_password": "admin123", "password": "new-pass-1"},
    )
    assert changed.status_code == 200
    assert client.get("/api/admin/me", headers=auth_headers).status_code == 401
    new_headers = {"Authorization": f"Bearer {changed.json()['token']}"}
    assert client.get("/api/admin/me", headers=new_headers).status_code == 200
    assert client.post("/api/admin/login", json={"username": "admin", "password": "admin123"}).status_code == 401
    assert client.post("/api/admin/login", json={"username": "admin", "password": "new-pass-1"}).status_code == 200


def test_update_admin_username_issues_new_token(client: TestClient, auth_headers: dict[str, str]) -> None:
    changed = client.patch(
        "/api/admin/me",
        headers=auth_headers,
        json={"current_password": "admin123", "username": "jesse"},
    )
    assert changed.status_code == 200
    assert changed.json()["username"] == "jesse"
    old_me = client.get("/api/admin/me", headers=auth_headers)
    assert old_me.status_code == 401
    new_headers = {"Authorization": f"Bearer {changed.json()['token']}"}
    assert client.get("/api/admin/me", headers=new_headers).json()["username"] == "jesse"
    assert client.post("/api/admin/login", json={"username": "jesse", "password": "admin123"}).status_code == 200


def test_update_admin_rejects_wrong_current_password(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.patch(
        "/api/admin/me",
        headers=auth_headers,
        json={"current_password": "nope-nope", "password": "new-pass-1"},
    )
    assert response.status_code == 400


def test_login_lockout_after_repeated_failures(client: TestClient) -> None:
    for _ in range(5):
        failed = client.post("/api/admin/login", json={"username": "admin", "password": "nope"})
        assert failed.status_code == 401
    locked = client.post("/api/admin/login", json={"username": "admin", "password": "nope"})
    assert locked.status_code == 429
    still_locked = client.post("/api/admin/login", json={"username": "admin", "password": "admin123"})
    assert still_locked.status_code == 429


def test_docs_and_openapi_are_disabled(client: TestClient) -> None:
    from app.main import app

    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None
    openapi = client.get("/openapi.json")
    if openapi.headers.get("content-type", "").startswith("application/json"):
        assert openapi.status_code != 200

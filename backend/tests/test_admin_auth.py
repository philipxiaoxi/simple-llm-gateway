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

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_permissions import PASSWORD, _create_role, _create_user, _login

BUILTIN = ["/", "/accounts", "/keys", "/logs", "/skills"]


def test_builtin_tabs_when_unset(client: TestClient, auth_headers: dict[str, str]) -> None:
    payload = client.get("/api/admin/me/nav-tabs", headers=auth_headers).json()
    assert payload["source"] == "builtin"
    assert payload["custom_paths"] is None
    assert payload["default_paths"] == BUILTIN
    assert payload["effective_paths"] == BUILTIN
    assert payload["max_tabs"] == 5
    assert payload["home_path"] == "/"
    assert any(item["path"] == "/mcp-plaza/keys" for item in payload["catalog"])


def test_user_tabs_override_default_and_reset(client: TestClient, auth_headers: dict[str, str]) -> None:
    saved = client.put(
        "/api/admin/settings/nav-tabs",
        headers=auth_headers,
        json={"paths": ["/", "/info", "/skills"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["source"] == "default"
    assert saved.json()["effective_paths"] == ["/", "/info", "/skills"]

    custom = client.put(
        "/api/admin/me/nav-tabs",
        headers=auth_headers,
        json={"paths": ["/mcp-plaza/calls", "/logs", "/logs"]},
    )
    assert custom.status_code == 200, custom.text
    body = custom.json()
    assert body["source"] == "custom"
    assert body["custom_paths"] == ["/mcp-plaza/calls", "/logs"]
    assert body["effective_paths"] == ["/mcp-plaza/calls", "/logs"]
    login = client.post("/api/admin/login", json={"username": "admin", "password": "admin123"})
    assert login.status_code == 200
    assert login.json()["home_path"] == "/mcp-plaza/calls"
    assert body["default_paths"] == ["/", "/info", "/skills"]

    reset = client.delete("/api/admin/me/nav-tabs", headers=auth_headers)
    assert reset.status_code == 200, reset.text
    assert reset.json()["source"] == "default"
    assert reset.json()["custom_paths"] is None
    assert reset.json()["effective_paths"] == ["/", "/info", "/skills"]


def test_custom_tabs_survive_default_update(client: TestClient, auth_headers: dict[str, str]) -> None:
    client.put("/api/admin/me/nav-tabs", headers=auth_headers, json={"paths": ["/logs"]})
    client.put("/api/admin/settings/nav-tabs", headers=auth_headers, json={"paths": ["/skills"]})
    body = client.get("/api/admin/me/nav-tabs", headers=auth_headers).json()
    assert body["source"] == "custom"
    assert body["effective_paths"] == ["/logs"]
    assert body["default_paths"] == ["/skills"]


def test_reject_invalid_tabs(client: TestClient, auth_headers: dict[str, str]) -> None:
    unknown = client.put("/api/admin/me/nav-tabs", headers=auth_headers, json={"paths": ["/nope"]})
    assert unknown.status_code == 400
    empty = client.put("/api/admin/me/nav-tabs", headers=auth_headers, json={"paths": []})
    assert empty.status_code == 400
    too_many = client.put(
        "/api/admin/me/nav-tabs",
        headers=auth_headers,
        json={"paths": ["/", "/accounts", "/keys", "/logs", "/skills", "/info"]},
    )
    assert too_many.status_code == 400


def test_user_cannot_save_forbidden_or_change_default(client: TestClient, auth_headers: dict[str, str]) -> None:
    role = _create_role(client, auth_headers, "仅记录", ["logs:view"])
    _create_user(client, auth_headers, "tab-reader", role["id"])
    reader = _login(client, "tab-reader", PASSWORD)

    own = client.put("/api/admin/me/nav-tabs", headers=reader, json={"paths": ["/logs"]})
    assert own.status_code == 200, own.text
    assert own.json()["effective_paths"] == ["/logs"]
    assert all(item["perm"] in {"", "logs:view"} or item["path"] == "/logs" for item in own.json()["catalog"])

    denied_page = client.put("/api/admin/me/nav-tabs", headers=reader, json={"paths": ["/accounts"]})
    assert denied_page.status_code == 400

    denied_default = client.put("/api/admin/settings/nav-tabs", headers=reader, json={"paths": ["/logs"]})
    assert denied_default.status_code == 403

    visible = client.get("/api/admin/settings/nav-tabs", headers=reader)
    assert visible.status_code == 200
    assert visible.json()["effective_paths"] == ["/logs"]

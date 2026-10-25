from __future__ import annotations

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.permissions import PAGE_ONLY_CODES, all_codes
from app.services.permission_guard import (
    EXEMPT_PATHS,
    KIND_AWARE_PREFIX,
    ROUTE_ANY_CODES,
    ROUTE_OVERRIDES,
    resolve_permission,
)

PASSWORD = "user-pass-123"


def _create_role(client: TestClient, auth: dict[str, str], name: str, permissions: list[str]) -> dict:
    response = client.post("/api/admin/roles", headers=auth, json={"name": name, "permissions": permissions})
    assert response.status_code == 201, response.text
    return response.json()


def _create_user(
    client: TestClient, auth: dict[str, str], username: str, role_id: int, password: str = PASSWORD
) -> dict:
    response = client.post(
        "/api/admin/users",
        headers=auth,
        json={"username": username, "password": password, "role_id": role_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, username: str, password: str = PASSWORD) -> dict[str, str]:
    response = client.post("/api/admin/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _has_admin_dep(route: APIRoute) -> bool:
    stack = [route.dependant]
    seen: set[int] = set()
    while stack:
        dependant = stack.pop()
        if id(dependant) in seen:
            continue
        seen.add(id(dependant))
        call = getattr(dependant, "call", None)
        if call is not None and getattr(call, "__name__", "") == "get_current_admin":
            return True
        stack.extend(dependant.dependencies)
    return False


# ---------------------------------------------------------------- 目录与映射完整性


def test_every_admin_route_resolves_a_catalog_permission() -> None:
    from app.main import app

    known = all_codes()
    unresolved: list[tuple[str, str]] = []
    unknown: list[tuple[str, str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/admin"):
            continue
        if not _has_admin_dep(route):
            continue
        if route.path in EXEMPT_PATHS or route.path.startswith(KIND_AWARE_PREFIX):
            continue
        for method in sorted(route.methods - {"HEAD"}):
            code = resolve_permission(method, route.path)
            if code is None:
                unresolved.append((method, route.path))
            elif code not in known:
                unknown.append((method, route.path, code))
    assert unresolved == [], f"以下管理接口缺少权限映射：{unresolved}"
    assert unknown == [], f"以下权限码不在目录内：{unknown}"


def test_override_codes_are_all_in_catalog() -> None:
    known = all_codes()
    stray = sorted({code for code in ROUTE_OVERRIDES.values() if code not in known})
    assert stray == []


def test_any_codes_are_all_in_catalog() -> None:
    known = all_codes()
    stray = sorted({code for codes in ROUTE_ANY_CODES.values() for code in codes if code not in known})
    assert stray == []


def test_catalog_codes_are_reachable() -> None:
    from app.main import app

    used: set[str] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/admin"):
            continue
        if not _has_admin_dep(route):
            continue
        if route.path in EXEMPT_PATHS or route.path.startswith(KIND_AWARE_PREFIX):
            continue
        for method in sorted(route.methods - {"HEAD"}):
            code = resolve_permission(method, route.path)
            if code:
                used.add(code)
    # 生成相关的权限码由按 kind 的依赖使用；benchmark:view 只控制页面入口
    kind_codes = {
        "image_gen:view",
        "image_gen:task_create",
        "image_gen:task_retry",
        "image_gen:task_delete",
        "video_gen:view",
        "video_gen:task_create",
        "video_gen:task_retry",
        "video_gen:task_delete",
    }
    unreachable = sorted(all_codes() - used - PAGE_ONLY_CODES - kind_codes)
    assert unreachable == [], f"目录里存在没有任何接口使用的权限码：{unreachable}"


# ---------------------------------------------------------------- 内置管理员角色


def test_builtin_admin_role_is_listed_and_locked(client: TestClient, auth_headers: dict[str, str]) -> None:
    roles = client.get("/api/admin/roles", headers=auth_headers).json()
    builtin = next(role for role in roles if role["builtin"])
    assert builtin["name"] == "管理员"
    assert builtin["permissions"] == ["*"]
    assert builtin["admin_count"] >= 1

    assert client.patch(
        f"/api/admin/roles/{builtin['id']}", headers=auth_headers, json={"name": "改名"}
    ).status_code == 403
    assert client.delete(f"/api/admin/roles/{builtin['id']}", headers=auth_headers).status_code == 403


def test_role_validation_and_references(client: TestClient, auth_headers: dict[str, str]) -> None:
    bad = client.post(
        "/api/admin/roles", headers=auth_headers, json={"name": "坏角色", "permissions": ["accounts:nope"]}
    )
    assert bad.status_code == 400
    assert "accounts:nope" in bad.text

    role = _create_role(client, auth_headers, "运维只读", ["logs:view"])
    _create_user(client, auth_headers, "ops-reader", role["id"])
    conflict = client.delete(f"/api/admin/roles/{role['id']}", headers=auth_headers)
    assert conflict.status_code == 409

    dup = client.post("/api/admin/roles", headers=auth_headers, json={"name": "运维只读", "permissions": []})
    assert dup.status_code == 409


# ---------------------------------------------------------------- 逐接口鉴权


def test_me_exposes_role_and_permissions(client: TestClient, auth_headers: dict[str, str]) -> None:
    me = client.get("/api/admin/me", headers=auth_headers).json()
    assert me["role"]["name"] == "管理员"
    assert me["role"]["builtin"] is True
    assert me["permissions"] == ["*"]

    role = _create_role(client, auth_headers, "记录只读", ["logs:view"])
    _create_user(client, auth_headers, "logs-reader", role["id"])
    reader = _login(client, "logs-reader")
    reader_me = client.get("/api/admin/me", headers=reader).json()
    assert reader_me["role"]["name"] == "记录只读"
    assert reader_me["permissions"] == ["logs:view"]


def test_permission_guard_denies_and_allows(client: TestClient, auth_headers: dict[str, str]) -> None:
    role = _create_role(client, auth_headers, "记录只读2", ["logs:view"])
    _create_user(client, auth_headers, "logs-reader2", role["id"])
    reader = _login(client, "logs-reader2")

    assert client.get("/api/admin/logs", headers=reader).status_code == 200

    denied = client.get("/api/admin/mcp/sites", headers=reader)
    assert denied.status_code == 403
    assert denied.json()["detail"]["error"]["permission"] == "mcp_sites:view"

    denied_write = client.post(
        "/api/admin/accounts", headers=reader, json={"name": "x", "provider": "deepseek", "api_key": "sk"}
    )
    assert denied_write.status_code == 403
    assert denied_write.json()["detail"]["error"]["permission"] == "accounts:create"


def test_role_permission_change_applies_without_relogin(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    role = _create_role(client, auth_headers, "动态权限", ["logs:view"])
    _create_user(client, auth_headers, "dynamic-user", role["id"])
    reader = _login(client, "dynamic-user")
    assert client.get("/api/admin/mcp/sites", headers=reader).status_code == 403

    patch = client.patch(
        f"/api/admin/roles/{role['id']}", headers=auth_headers, json={"permissions": ["logs:view", "mcp_sites:view"]}
    )
    assert patch.status_code == 200
    assert client.get("/api/admin/mcp/sites", headers=reader).status_code == 200


# ---------------------------------------------------------------- 防提权与保护


def test_non_admin_cannot_grant_permissions_it_lacks(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    role = _create_role(client, auth_headers, "角色管理员(受限)", ["roles:view", "roles:create", "logs:view"])
    _create_user(client, auth_headers, "role-maker", role["id"])
    maker = _login(client, "role-maker")

    escalate = client.post(
        "/api/admin/roles", headers=maker, json={"name": "越权角色", "permissions": ["accounts:delete"]}
    )
    assert escalate.status_code == 403
    assert "accounts:delete" in escalate.text

    allowed = client.post(
        "/api/admin/roles", headers=maker, json={"name": "同权角色", "permissions": ["logs:view"]}
    )
    assert allowed.status_code == 201


def test_non_admin_cannot_assign_builtin_role(client: TestClient, auth_headers: dict[str, str]) -> None:
    builtin_id = next(r["id"] for r in client.get("/api/admin/roles", headers=auth_headers).json() if r["builtin"])
    role = _create_role(client, auth_headers, "用户管理员(受限)", ["users:view", "users:create"])
    _create_user(client, auth_headers, "user-maker", role["id"])
    maker = _login(client, "user-maker")

    denied = client.post(
        "/api/admin/users",
        headers=maker,
        json={"username": "escalated", "password": PASSWORD, "role_id": builtin_id},
    )
    assert denied.status_code == 403


def test_last_admin_cannot_be_removed_or_demoted(client: TestClient, auth_headers: dict[str, str]) -> None:
    me = client.get("/api/admin/me", headers=auth_headers).json()
    admin_id = client.get("/api/admin/users", headers=auth_headers).json()[0]["id"]

    assert client.delete(f"/api/admin/users/{admin_id}", headers=auth_headers).status_code == 409

    limited = _create_role(client, auth_headers, "降级目标", ["logs:view"])
    demote = client.patch(
        f"/api/admin/users/{admin_id}", headers=auth_headers, json={"role_id": limited["id"]}
    )
    assert demote.status_code == 409
    assert me["username"]


def test_non_admin_cannot_manage_stronger_accounts(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    role = _create_role(
        client,
        auth_headers,
        "用户运维(受限)",
        [
            "users:view",
            "users:update",
            "users:delete",
            "users:reset_password",
            "users:toggle_status",
        ],
    )
    _create_user(client, auth_headers, "user-ops", role["id"])
    ops = _login(client, "user-ops")

    users = client.get("/api/admin/users", headers=auth_headers).json()
    admin_id = next(user["id"] for user in users if user["role_builtin"])

    assert client.post(
        f"/api/admin/users/{admin_id}/password", headers=ops, json={"password": "hacked-pass-1"}
    ).status_code == 403
    assert client.post(
        f"/api/admin/users/{admin_id}/status", headers=ops, json={"enabled": False}
    ).status_code == 403
    assert client.delete(f"/api/admin/users/{admin_id}", headers=ops).status_code == 403

    stronger = _create_role(client, auth_headers, "更强角色", ["users:view", "roles:view"])
    stronger_user = _create_user(client, auth_headers, "stronger-user", stronger["id"])
    assert client.post(
        f"/api/admin/users/{stronger_user['id']}/password", headers=ops, json={"password": "hacked-pass-2"}
    ).status_code == 403


def test_disabled_user_session_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    role = _create_role(client, auth_headers, "待停用", ["logs:view"])
    user = _create_user(client, auth_headers, "disable-me", role["id"])
    reader = _login(client, "disable-me")
    assert client.get("/api/admin/logs", headers=reader).status_code == 200

    assert client.post(f"/api/admin/users/{user['id']}/status", headers=auth_headers, json={"enabled": False}).status_code == 200
    assert client.get("/api/admin/logs", headers=reader).status_code == 401
    assert client.post("/api/admin/login", json={"username": "disable-me", "password": PASSWORD}).status_code == 403


# ---------------------------------------------------------------- 图片 / 视频分权


def test_generation_tasks_split_by_kind(client: TestClient, auth_headers: dict[str, str]) -> None:
    role = _create_role(client, auth_headers, "只会画图", ["image_gen:view", "image_gen:task_create"])
    _create_user(client, auth_headers, "image-only", role["id"])
    painter = _login(client, "image-only")

    video = client.post(
        "/api/admin/generation/tasks",
        headers=painter,
        json={"kind": "video", "model": "m", "prompt": "p", "provider": "p"},
    )
    assert video.status_code == 403
    assert video.json()["detail"]["error"]["permission"] == "video_gen:task_create"

    image = client.post(
        "/api/admin/generation/tasks",
        headers=painter,
        json={"kind": "image", "model": "m", "prompt": "p", "provider": "p"},
    )
    assert image.status_code != 403

    tasks = client.get("/api/admin/generation/tasks", headers=painter).json()
    assert all(item["kind"] == "image" for item in tasks["items"])


# ---------------------------------------------------------------- MCP 广场按功能分权


def test_mcp_catalog_plaza_scope_follows_capability_permissions(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    role = _create_role(client, auth_headers, "广场只读", ["dashboard:view", "mcp_douyin:view"])
    _create_user(client, auth_headers, "plaza-reader", role["id"])
    reader = _login(client, "plaza-reader")

    plaza = {
        row["capability_id"]
        for row in client.get("/api/admin/mcp/catalog?scope=plaza", headers=reader).json()["items"]
    }
    assert "douyin" in plaza
    assert "knowledge" not in plaza
    assert "diagram" not in plaza

    everything = {
        row["capability_id"]
        for row in client.get("/api/admin/mcp/catalog?scope=all", headers=reader).json()["items"]
    }
    assert {"douyin", "knowledge", "diagram"} <= everything

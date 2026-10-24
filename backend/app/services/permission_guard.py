"""管理接口的功能级权限校验。

`permissions.py` 是权限码的单一真源；本模块把「HTTP 方法 + 路由模板」映射到权限码，
提供一个全局依赖 `require_admin_permission`，以及生成任务用的按 `kind` 校验的依赖。

映射规则：

1. 先查 `ROUTE_OVERRIDES`（显式登记的特殊动作，如 export / probe / run / download）；
2. 否则按路由前缀找到资源，用 HTTP 方法推导默认动作
   （`GET → view`、`POST → create`、`PUT/PATCH → update`、`DELETE → delete`）。

未命中映射的管理路由**放行**（不因为漏登记而误伤线上），但 `tests/test_permissions.py`
会断言除放行清单外每条 `/api/admin/*` 路由都能解析出合法权限码，漏登记会在测试里失败。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_admin
from app.models import Admin, GenerationTask, Role
from app.permissions import WILDCARD, code_label, validate_codes

DEFAULT_ACTIONS = {
    "GET": "view",
    "POST": "create",
    "PUT": "update",
    "PATCH": "update",
    "DELETE": "delete",
}

# 路由前缀 -> 资源（按最长前缀匹配）
RESOURCE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/api/admin/dashboard", "dashboard"),
    ("/api/admin/providers", "accounts"),
    ("/api/admin/accounts", "accounts"),
    ("/api/admin/agents", "agents"),
    ("/api/admin/keys", "keys"),
    ("/api/admin/logs", "logs"),
    ("/api/admin/benchmark/groups", "benchmark_groups"),
    ("/api/admin/benchmark/history", "benchmark_history"),
    ("/api/admin/benchmark/leaderboard", "leaderboard"),
    ("/api/admin/benchmark", "benchmark"),
    ("/api/admin/leaderboard", "leaderboard"),
    ("/api/admin/info", "info"),
    ("/api/admin/generation", "generation"),
    ("/api/admin/offline", "offline"),
    ("/api/admin/skills/categories", "skills"),
    ("/api/admin/skills/classification-settings", "skills"),
    ("/api/admin/skills", "skills"),
    ("/api/admin/skill-bundles", "skill_bundles"),
    ("/api/admin/security", "security"),
    ("/api/admin/mcp/catalog", "mcp_catalog"),
    ("/api/admin/mcp/calls", "mcp_calls"),
    ("/api/admin/mcp/keys", "mcp_keys"),
    ("/api/admin/mcp/knowledge", "mcp_knowledge"),
    ("/api/admin/mcp/docparse", "mcp_docparse"),
    ("/api/admin/mcp/sites", "mcp_sites"),
    ("/api/admin/mcp/douyin", "mcp_douyin"),
    ("/api/admin/mcp/diagrams", "mcp_diagrams"),
    ("/api/admin/tools", "tools"),
    ("/api/admin/jobs", "jobs"),
    ("/api/admin/voice", "voice"),
    ("/api/admin/integrations", "integrations"),
    ("/api/admin/public-gate", "public_gate"),
    ("/api/admin/users", "users"),
    ("/api/admin/roles", "roles"),
    ("/api/admin/permission-catalog", "roles"),
)

# (方法, 路由模板) -> 权限码。仅登记「默认动作不对」或需要合并到某个动作的接口。
ROUTE_OVERRIDES: dict[tuple[str, str], str] = {
    # 上游账号
    ("POST", "/api/admin/accounts/export"): "accounts:export",
    ("POST", "/api/admin/accounts/import"): "accounts:import",
    ("POST", "/api/admin/accounts/{account_id}/probe"): "accounts:probe",
    ("POST", "/api/admin/accounts/{account_id}/quota"): "accounts:quota",
    ("POST", "/api/admin/accounts/{account_id}/models"): "accounts:models_manage",
    ("POST", "/api/admin/accounts/{account_id}/models/custom"): "accounts:models_manage",
    ("PATCH", "/api/admin/accounts/{account_id}/models/{model_id:path}"): "accounts:models_manage",
    ("DELETE", "/api/admin/accounts/{account_id}/models/custom/{model_id:path}"): "accounts:models_manage",
    ("GET", "/api/admin/accounts/{account_id}/oauth/start"): "accounts:update",
    # API Key
    ("GET", "/api/admin/keys/{key_id}/cc-switch"): "keys:cc_switch",
    ("POST", "/api/admin/keys/{key_id}/cc-switch"): "keys:cc_switch",
    ("GET", "/api/admin/keys/{key_id}/aliases"): "keys:aliases_manage",
    ("POST", "/api/admin/keys/{key_id}/aliases"): "keys:aliases_manage",
    ("POST", "/api/admin/keys/{key_id}/aliases/rename"): "keys:aliases_manage",
    ("DELETE", "/api/admin/keys/{key_id}/aliases/{alias:path}"): "keys:aliases_manage",
    # 记录审计
    ("GET", "/api/admin/logs/{log_id}"): "logs:detail",
    ("GET", "/api/admin/logs/{log_id}/export"): "logs:export",
    ("GET", "/api/admin/logs/{log_id}/messages"): "logs:messages",
    ("GET", "/api/admin/logs/{log_id}/messages/{seq}"): "logs:messages",
    ("GET", "/api/admin/logs/{log_id}/reasoning/{call_id}"): "logs:messages",
    ("GET", "/api/admin/logs/{log_id}/turns"): "logs:messages",
    # 网关代理
    ("POST", "/api/admin/agents/{agent_id}/routes/{route_id}/models"): "agents:route_bind",
    # 模型测速 / 榜单 / 历史
    ("POST", "/api/admin/benchmark/groups"): "benchmark_groups:manage",
    ("PATCH", "/api/admin/benchmark/groups/{group_id}"): "benchmark_groups:manage",
    ("DELETE", "/api/admin/benchmark/groups/{group_id}"): "benchmark_groups:manage",
    ("POST", "/api/admin/benchmark"): "benchmark:run",
    ("POST", "/api/admin/benchmark/history"): "benchmark:run",
    ("GET", "/api/admin/benchmark/history/export"): "benchmark_history:export",
    ("GET", "/api/admin/benchmark/history/{run_id}"): "benchmark_history:detail",
    ("GET", "/api/admin/benchmark/leaderboard"): "leaderboard:view",
    ("GET", "/api/admin/benchmark/leaderboard/export"): "leaderboard:export",
    # 资讯收集
    ("GET", "/api/admin/info/items/{item_id}"): "info:view",
    ("GET", "/api/admin/info/stats"): "info:view",
    ("GET", "/api/admin/info/ai/settings"): "info:ai_settings",
    ("PUT", "/api/admin/info/ai/settings"): "info:ai_settings",
    ("POST", "/api/admin/info/ai/rescore"): "info:rescore",
    ("POST", "/api/admin/info/ai/skip"): "info:ai_skip",
    ("POST", "/api/admin/info/articles"): "info:article_create",
    ("POST", "/api/admin/info/items/delete"): "info:article_delete",
    ("DELETE", "/api/admin/info/items/{item_id}"): "info:article_delete",
    ("POST", "/api/admin/info/items/{item_id}/ai-skip"): "info:ai_skip",
    ("POST", "/api/admin/info/items/{item_id}/favorite"): "info:favorite",
    ("POST", "/api/admin/info/items/{item_id}/featured"): "info:featured",
    ("POST", "/api/admin/info/items/{item_id}/hidden"): "info:hidden",
    ("POST", "/api/admin/info/sources"): "info:source_create",
    ("PATCH", "/api/admin/info/sources/{source_id}"): "info:source_update",
    ("DELETE", "/api/admin/info/sources/{source_id}"): "info:source_delete",
    ("POST", "/api/admin/info/sources/preview"): "info:preview",
    ("POST", "/api/admin/info/sources/batch-interval"): "info:source_update",
    ("POST", "/api/admin/info/sources/collect-batch"): "info:collect",
    ("POST", "/api/admin/info/sources/{source_id}/collect"): "info:collect",
    # 生成（kind 之外的共享面）
    ("GET", "/api/admin/generation/settings"): "generation:settings",
    ("PUT", "/api/admin/generation/settings"): "generation:settings",
    ("POST", "/api/admin/generation/providers/{provider}/key"): "generation:settings",
    ("POST", "/api/admin/generation/uploads"): "generation:upload",
    ("DELETE", "/api/admin/generation/uploads/{asset_id}"): "generation:upload",
    # 离线下载
    ("POST", "/api/admin/offline/cache/{item_id}/refresh"): "offline:retry",
    ("DELETE", "/api/admin/offline/cache/{item_id}"): "offline:task_delete",
    ("POST", "/api/admin/offline/chrome/cache"): "offline:task_create",
    ("POST", "/api/admin/offline/docker/cache"): "offline:task_create",
    ("POST", "/api/admin/offline/edge/cache"): "offline:task_create",
    ("POST", "/api/admin/offline/msstore/cache"): "offline:task_create",
    ("POST", "/api/admin/offline/vscode/cache"): "offline:task_create",
    ("POST", "/api/admin/offline/vscode/query"): "offline:view",
    ("GET", "/api/admin/offline/cache/{item_id}/download"): "offline:view",
    # Skills
    ("POST", "/api/admin/skills/upload"): "skills:upload",
    ("POST", "/api/admin/skills/upload-url"): "skills:upload",
    ("POST", "/api/admin/skills/bulk-update"): "skills:upload",
    ("POST", "/api/admin/skills/{skill_id}/replace"): "skills:upload",
    ("POST", "/api/admin/skills/{skill_id}/analysis"): "skills:analysis",
    ("GET", "/api/admin/skills/{skill_id}/download"): "skills:download",
    ("GET", "/api/admin/skills/{skill_id}/files/{file_path:path}"): "skills:download",
    ("POST", "/api/admin/skills/{skill_id}/download-url"): "skills:download",
    ("GET", "/api/admin/skills/classification-settings"): "skills:view",
    ("PUT", "/api/admin/skills/classification-settings"): "skills:update",
    ("GET", "/api/admin/skills/categories"): "skills:view",
    ("POST", "/api/admin/skills/categories"): "skills:category_manage",
    ("PATCH", "/api/admin/skills/categories/{category_id}"): "skills:category_manage",
    ("DELETE", "/api/admin/skills/categories/{category_id}"): "skills:category_manage",
    # Skills 组合包
    ("POST", "/api/admin/skill-bundles"): "skill_bundles:manage",
    ("POST", "/api/admin/skill-bundles/"): "skill_bundles:manage",
    ("PATCH", "/api/admin/skill-bundles/{bundle_id}"): "skill_bundles:manage",
    ("DELETE", "/api/admin/skill-bundles/{bundle_id}"): "skill_bundles:manage",
    ("POST", "/api/admin/skill-bundles/{bundle_id}/members"): "skill_bundles:manage",
    ("DELETE", "/api/admin/skill-bundles/{bundle_id}/members/{skill_id}"): "skill_bundles:manage",
    ("POST", "/api/admin/skill-bundles/{bundle_id}/download-url"): "skill_bundles:manage",
    ("GET", "/api/admin/skill-bundles/{bundle_id}/download"): "skill_bundles:view",
    # 安全中心
    ("GET", "/api/admin/security/events"): "security:view",
    ("GET", "/api/admin/security/events/{event_id}"): "security:event_detail",
    ("GET", "/api/admin/security/overview"): "security:view",
    ("GET", "/api/admin/security/settings"): "security:view",
    ("PUT", "/api/admin/security/settings"): "security:settings",
    # MCP Key
    ("GET", "/api/admin/mcp/keys"): "mcp_keys:view",
    ("GET", "/api/admin/mcp/keys/usage"): "mcp_keys:view",
    ("GET", "/api/admin/mcp/keys/{key_id}/reveal"): "mcp_keys:reveal",
    ("GET", "/api/admin/mcp/keys/{key_id}/integration"): "mcp_keys:view",
    ("POST", "/api/admin/mcp/keys"): "mcp_keys:create",
    ("PATCH", "/api/admin/mcp/keys/{key_id}"): "mcp_keys:update",
    ("DELETE", "/api/admin/mcp/keys/{key_id}"): "mcp_keys:delete",
    ("PUT", "/api/admin/mcp/keys/{key_id}/capabilities"): "mcp_keys:capabilities_manage",
    ("PUT", "/api/admin/mcp/keys/{key_id}/capabilities/{capability_id}/config"): "mcp_keys:capabilities_manage",
    # MCP 知识库
    ("GET", "/api/admin/mcp/knowledge/bases"): "mcp_knowledge:view",
    ("GET", "/api/admin/mcp/knowledge/bases/{kb_id}/documents"): "mcp_knowledge:view",
    ("GET", "/api/admin/mcp/knowledge/embedding-accounts"): "mcp_knowledge:view",
    ("GET", "/api/admin/mcp/knowledge/jobs"): "mcp_knowledge:view",
    ("GET", "/api/admin/mcp/knowledge/jobs/{job_id}"): "mcp_knowledge:view",
    ("POST", "/api/admin/mcp/knowledge/bases"): "mcp_knowledge:kb_create",
    ("PATCH", "/api/admin/mcp/knowledge/bases/{kb_id}"): "mcp_knowledge:kb_update",
    ("POST", "/api/admin/mcp/knowledge/bases/{kb_id}/reindex"): "mcp_knowledge:kb_update",
    ("DELETE", "/api/admin/mcp/knowledge/bases/{kb_id}"): "mcp_knowledge:kb_delete",
    ("POST", "/api/admin/mcp/knowledge/bases/{kb_id}/documents"): "mcp_knowledge:doc_upload",
    ("POST", "/api/admin/mcp/knowledge/jobs"): "mcp_knowledge:doc_upload",
    ("POST", "/api/admin/mcp/knowledge/jobs/upload"): "mcp_knowledge:doc_upload",
    ("POST", "/api/admin/mcp/knowledge/jobs/upload-batch"): "mcp_knowledge:doc_upload",
    ("POST", "/api/admin/mcp/knowledge/jobs/reembed"): "mcp_knowledge:doc_reembed",
    ("POST", "/api/admin/mcp/knowledge/jobs/{job_id}/retry"): "mcp_knowledge:doc_upload",
    ("POST", "/api/admin/mcp/knowledge/jobs/{job_id}/cancel"): "mcp_knowledge:doc_upload",
    ("POST", "/api/admin/mcp/knowledge/bases/{kb_id}/documents/{doc_id}/reembed"): "mcp_knowledge:doc_reembed",
    ("DELETE", "/api/admin/mcp/knowledge/bases/{kb_id}/documents/{doc_id}"): "mcp_knowledge:doc_delete",
    ("DELETE", "/api/admin/mcp/knowledge/jobs/{job_id}"): "mcp_knowledge:doc_delete",
    ("POST", "/api/admin/mcp/knowledge/bases/{kb_id}/search"): "mcp_knowledge:search",
    # MCP 文档解析
    ("GET", "/api/admin/mcp/docparse/jobs"): "mcp_docparse:view",
    ("GET", "/api/admin/mcp/docparse/jobs/{job_id}"): "mcp_docparse:view",
    ("GET", "/api/admin/mcp/docparse/jobs/{job_id}/download"): "mcp_docparse:download",
    ("POST", "/api/admin/mcp/docparse/jobs"): "mcp_docparse:job_create",
    ("POST", "/api/admin/mcp/docparse/jobs/{job_id}/cancel"): "mcp_docparse:job_cancel",
    ("POST", "/api/admin/mcp/docparse/jobs/{job_id}/retry"): "mcp_docparse:job_retry",
    ("POST", "/api/admin/mcp/docparse/jobs/{job_id}/ingest"): "mcp_docparse:ingest",
    ("DELETE", "/api/admin/mcp/docparse/jobs/{job_id}"): "mcp_docparse:job_delete",
    # MCP 站点
    ("GET", "/api/admin/mcp/sites"): "mcp_sites:view",
    ("GET", "/api/admin/mcp/sites/{site_id}"): "mcp_sites:view",
    ("GET", "/api/admin/mcp/sites/{site_id}/versions/{version_id}"): "mcp_sites:view",
    ("GET", "/api/admin/mcp/sites/{site_id}/token"): "mcp_sites:token_manage",
    ("POST", "/api/admin/mcp/sites"): "mcp_sites:site_create",
    ("PATCH", "/api/admin/mcp/sites/{site_id}"): "mcp_sites:site_update",
    ("DELETE", "/api/admin/mcp/sites/{site_id}"): "mcp_sites:site_delete",
    ("POST", "/api/admin/mcp/sites/{site_id}/versions"): "mcp_sites:version_create",
    ("POST", "/api/admin/mcp/sites/{site_id}/versions/{version_id}/activate"): "mcp_sites:version_activate",
    ("POST", "/api/admin/mcp/sites/{site_id}/versions/{version_id}/retry"): "mcp_sites:version_retry",
    ("DELETE", "/api/admin/mcp/sites/{site_id}/versions/{version_id}"): "mcp_sites:version_delete",
    ("POST", "/api/admin/mcp/sites/{site_id}/token"): "mcp_sites:token_manage",
    # MCP 抖音
    ("GET", "/api/admin/mcp/douyin/jobs"): "mcp_douyin:view",
    ("GET", "/api/admin/mcp/douyin/jobs/{job_id}"): "mcp_douyin:view",
    ("GET", "/api/admin/mcp/douyin/provider"): "mcp_douyin:view",
    ("PUT", "/api/admin/mcp/douyin/provider"): "mcp_douyin:provider_manage",
    ("DELETE", "/api/admin/mcp/douyin/provider"): "mcp_douyin:provider_manage",
    ("POST", "/api/admin/mcp/douyin/jobs"): "mcp_douyin:job_create",
    ("POST", "/api/admin/mcp/douyin/jobs/{job_id}/retry"): "mcp_douyin:job_retry",
    ("DELETE", "/api/admin/mcp/douyin/jobs/{job_id}"): "mcp_douyin:job_delete",
    # MCP 图表
    ("GET", "/api/admin/mcp/diagrams"): "mcp_diagrams:view",
    ("GET", "/api/admin/mcp/diagrams/{site_id}"): "mcp_diagrams:view",
    ("GET", "/api/admin/mcp/diagrams/{site_id}/source"): "mcp_diagrams:view",
    ("GET", "/api/admin/mcp/diagrams/{site_id}/token"): "mcp_diagrams:token_manage",
    ("POST", "/api/admin/mcp/diagrams"): "mcp_diagrams:generate",
    ("PATCH", "/api/admin/mcp/diagrams/{site_id}"): "mcp_diagrams:update",
    ("POST", "/api/admin/mcp/diagrams/{site_id}/rollback"): "mcp_diagrams:rollback",
    ("POST", "/api/admin/mcp/diagrams/{site_id}/token"): "mcp_diagrams:token_manage",
    ("DELETE", "/api/admin/mcp/diagrams/{site_id}"): "mcp_diagrams:delete",
    # 工具中心
    ("GET", "/api/admin/tools"): "tools:view",
    ("GET", "/api/admin/tools/{tool_id}/events"): "tools:view",
    ("GET", "/api/admin/tools/{tool_id}/runs"): "tools:view",
    ("GET", "/api/admin/tools/{tool_id}/runs/{run_id}"): "tools:view",
    ("GET", "/api/admin/tools/{tool_id}/script"): "tools:script_edit",
    ("POST", "/api/admin/tools"): "tools:create",
    ("PATCH", "/api/admin/tools/{tool_id}"): "tools:update",
    ("DELETE", "/api/admin/tools/{tool_id}"): "tools:delete",
    ("POST", "/api/admin/tools/{tool_id}/script"): "tools:script_edit",
    ("POST", "/api/admin/tools/{tool_id}/pre-download"): "tools:run",
    ("POST", "/api/admin/tools/{tool_id}/download-url"): "tools:run",
    ("POST", "/api/admin/tools/{tool_id}/stop"): "tools:stop",
    # 定时任务
    ("GET", "/api/admin/jobs"): "jobs:view",
    ("POST", "/api/admin/jobs/{job_id}/run"): "jobs:run",
    ("PATCH", "/api/admin/jobs/{job_id}"): "jobs:edit",
    # 语音房
    ("GET", "/api/admin/voice/rooms"): "voice:view",
    ("GET", "/api/admin/voice/rooms/{room_id}"): "voice:view",
    ("GET", "/api/admin/voice/rooms/{room_id}/events"): "voice:view",
    ("GET", "/api/admin/voice/rooms/{room_id}/live"): "voice:view",
    ("GET", "/api/admin/voice/rooms/{room_id}/segments"): "voice:view",
    ("GET", "/api/admin/voice/rooms/{room_id}/sessions"): "voice:view",
    ("GET", "/api/admin/voice/polish-accounts"): "voice:view",
    ("GET", "/api/admin/voice/storage"): "voice:view",
    ("POST", "/api/admin/voice/rooms"): "voice:manage",
    ("PATCH", "/api/admin/voice/rooms/{room_id}"): "voice:manage",
    ("POST", "/api/admin/voice/rooms/{room_id}/token"): "voice:manage",
    ("POST", "/api/admin/voice/rooms/{room_id}/rotate-code"): "voice:manage",
    ("POST", "/api/admin/voice/cleanup"): "voice:manage",
    ("POST", "/api/admin/voice/test-polish"): "voice:manage",
    ("DELETE", "/api/admin/voice/rooms/{room_id}"): "voice:manage",
    # 用户管理
    ("GET", "/api/admin/users"): "users:view",
    ("POST", "/api/admin/users"): "users:create",
    ("PATCH", "/api/admin/users/{admin_id}"): "users:update",
    ("DELETE", "/api/admin/users/{admin_id}"): "users:delete",
    ("POST", "/api/admin/users/{admin_id}/password"): "users:reset_password",
    ("POST", "/api/admin/users/{admin_id}/status"): "users:toggle_status",
    # 角色管理
    ("GET", "/api/admin/roles"): "roles:view",
    ("POST", "/api/admin/roles"): "roles:create",
    ("PATCH", "/api/admin/roles/{role_id}"): "roles:update",
    ("DELETE", "/api/admin/roles/{role_id}"): "roles:delete",
    # 第三方集成 / 公开门禁
    ("GET", "/api/admin/integrations/tikhub"): "integrations:view",
    ("PUT", "/api/admin/integrations/tikhub"): "integrations:update",
    ("DELETE", "/api/admin/integrations/tikhub"): "integrations:update",
    ("GET", "/api/admin/public-gate"): "public_gate:view",
    ("GET", "/api/admin/public-gate/sessions"): "public_gate:view",
    ("PUT", "/api/admin/public-gate"): "public_gate:update",
    # 权限目录（角色编辑页读取）
    ("GET", "/api/admin/permission-catalog"): "roles:view",
}

# 权限校验放行：登录、当前账号、OAuth 回调、前端埋点。这些接口要么不挂在守卫路由下，
# 要么只记审计事件，不带功能语义。
EXEMPT_PATHS = frozenset(
    {
        "/api/admin/login",
        "/api/admin/me",
        "/api/admin/oauth/grok/callback",
        "/api/admin/security/beacon",
    }
)

# 生成任务的按 kind 校验在 admin_generation 路由内完成，全局守卫跳过这几条。
KIND_AWARE_PREFIX = "/api/admin/generation/tasks"

_ALLOWED_METHODS = frozenset(DEFAULT_ACTIONS)


def resource_for_path(path: str) -> str | None:
    for prefix, resource in RESOURCE_PREFIXES:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "{"):
            return resource
    return None


def resolve_permission(method: str, path: str) -> str | None:
    """把「方法 + 路由模板」映射成权限码；无法映射时返回 None。"""
    method = method.upper()
    override = ROUTE_OVERRIDES.get((method, path))
    if override is not None:
        return override
    resource = resource_for_path(path)
    action = DEFAULT_ACTIONS.get(method)
    if resource is None or action is None:
        return None
    return f"{resource}:{action}"


def parse_permissions(raw: str | None) -> set[str]:
    import json

    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    return {item for item in data if isinstance(item, str) and item}


def permissions_for_admin(db: Session, admin: Admin) -> set[str]:
    """当前账号的权限集合。无角色引用视为通配（升级过渡）；引用的角色缺失时按拒绝处理。"""
    role_id = getattr(admin, "role_id", None)
    if not role_id:
        return {WILDCARD}
    role = db.get(Role, role_id)
    if role is None:
        return set()
    permissions = parse_permissions(role.permissions_json)
    if role.builtin:
        permissions.add(WILDCARD)
    return permissions


def has_permission(permissions: set[str], code: str) -> bool:
    return WILDCARD in permissions or code in permissions


def permission_denied(code: str) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={
            "error": {
                "type": "permission_denied",
                "message": f"无权限：{code_label(code)}",
                "permission": code,
            }
        },
    )


def _route_template(request: Request) -> str | None:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else None


def require_admin_permission(
    request: Request,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> None:
    """全局权限守卫：挂在管理路由的父路由上（见 `app/main.py`）。"""
    path = _route_template(request)
    if path is None or not path.startswith("/api/admin") or path in EXEMPT_PATHS:
        return
    if path.startswith(KIND_AWARE_PREFIX):
        return
    code = resolve_permission(request.method, path)
    if code is None:
        return
    if has_permission(permissions_for_admin(db, admin), code):
        return
    raise permission_denied(code)


def require_permission(code: str) -> Callable[[Admin, Session], None]:
    """显式权限依赖，用于按请求体/资源归属判定的接口。"""

    def dependency(
        admin: Admin = Depends(get_current_admin),
        db: Session = Depends(get_db),
    ) -> None:
        if has_permission(permissions_for_admin(db, admin), code):
            return
        raise permission_denied(code)

    return dependency


def _generation_resource(kind: str) -> str | None:
    if kind == "image":
        return "image_gen"
    if kind == "video":
        return "video_gen"
    return None


async def require_generation_task_create(
    request: Request,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> None:
    body: Any = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    kind = str(body.get("kind") or "") if isinstance(body, dict) else ""
    resource = _generation_resource(kind)
    code = f"{resource}:task_create" if resource else "image_gen:task_create"
    if has_permission(permissions_for_admin(db, admin), code):
        return
    raise permission_denied(code)


def require_generation_task_access(action: str) -> Callable[..., None]:
    """按任务自身的 kind 校验（查看/重试/删除）。"""

    def dependency(
        task_id: str,
        admin: Admin = Depends(get_current_admin),
        db: Session = Depends(get_db),
    ) -> None:
        task = db.get(GenerationTask, task_id)
        resource = _generation_resource(getattr(task, "kind", "") or "")
        code = f"{resource}:{action}" if resource else f"generation:{action}"
        if has_permission(permissions_for_admin(db, admin), code):
            return
        raise permission_denied(code)

    return dependency


def allowed_generation_kinds(db: Session, admin: Admin) -> set[str]:
    permissions = permissions_for_admin(db, admin)
    kinds: set[str] = set()
    if has_permission(permissions, "image_gen:view"):
        kinds.add("image")
    if has_permission(permissions, "video_gen:view"):
        kinds.add("video")
    return kinds


def require_generation_view(
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> None:
    if allowed_generation_kinds(db, admin):
        return
    raise permission_denied("image_gen:view")


def assert_catalog_consistency() -> list[str]:
    """返回目录与覆盖表之间的不一致项（供测试与启动自检使用）。"""
    problems: list[str] = []
    unknown = validate_codes(set(ROUTE_OVERRIDES.values()))
    problems.extend(f"覆盖表登记了目录外的权限码：{code}" for code in unknown)
    return problems

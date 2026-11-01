"""移动端底部 Tab 的候选目录、校验与按用户解析。"""

from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models import Admin, NavTabPreference
from app.permissions import WILDCARD
from app.services.permission_guard import permissions_for_admin

MAX_TABS = 5
SCOPE_DEFAULT = "default"
SCOPE_USER = "user"

# 与前端 NAV_LINKS + PLAZA_APP_LINKS 对齐。perm 为空表示登录即可。
NAV_TAB_CATALOG: tuple[dict[str, str], ...] = (
    {"path": "/", "label": "概览", "perm": "dashboard:view"},
    {"path": "/accounts", "label": "上游账号", "perm": "accounts:view"},
    {"path": "/agents", "label": "网关代理", "perm": "agents:view"},
    {"path": "/keys", "label": "API Key", "perm": "keys:view"},
    {"path": "/logs", "label": "记录审计", "perm": "logs:view"},
    {"path": "/benchmark", "label": "模型测速", "perm": "benchmark:view"},
    {"path": "/benchmark/history", "label": "测速历史", "perm": "benchmark_history:view"},
    {"path": "/leaderboard", "label": "模型榜", "perm": "leaderboard:view"},
    {"path": "/info", "label": "资讯收集", "perm": "info:view"},
    {"path": "/image-gen", "label": "图片生成", "perm": "image_gen:view"},
    {"path": "/video-gen", "label": "视频生成", "perm": "video_gen:view"},
    {"path": "/offline", "label": "离线下载", "perm": "offline:view"},
    {"path": "/skills", "label": "Skills", "perm": "skills:view"},
    {"path": "/security", "label": "安全中心", "perm": "security:view"},
    {"path": "/mcp-plaza", "label": "MCP 广场", "perm": "mcp_catalog:view"},
    {"path": "/tools", "label": "工具中心", "perm": "tools:view"},
    {"path": "/jobs", "label": "定时任务", "perm": "jobs:view"},
    {"path": "/voice", "label": "语音房", "perm": "voice:view"},
    {"path": "/users", "label": "用户管理", "perm": "users:view"},
    {"path": "/roles", "label": "角色管理", "perm": "roles:view"},
    {"path": "/mcp-plaza/knowledge", "label": "知识库", "perm": "mcp_knowledge:view"},
    {"path": "/mcp-plaza/docparse", "label": "文档转 Markdown", "perm": "mcp_docparse:view"},
    {"path": "/mcp-plaza/sites", "label": "站点部署", "perm": "mcp_sites:view"},
    {"path": "/mcp-plaza/douyin", "label": "抖音视频下载", "perm": "mcp_douyin:view"},
    {"path": "/mcp-plaza/portraits", "label": "人像参考库", "perm": "mcp_portraits:view"},
    {"path": "/mcp-plaza/poses", "label": "姿势参考库", "perm": "mcp_poses:view"},
    {"path": "/mcp-plaza/costumes", "label": "服装参考库", "perm": "mcp_costumes:view"},
    {"path": "/mcp-plaza/diagrams", "label": "图表生成", "perm": "mcp_diagrams:view"},
    {"path": "/mcp-plaza/generation", "label": "图片 / 视频生成配置", "perm": "generation:settings"},
    {"path": "/mcp-plaza/info-report", "label": "资讯上报", "perm": "mcp_info:view"},
    {"path": "/mcp-plaza/docs", "label": "MCP 接入说明", "perm": "mcp_catalog:view"},
    {"path": "/mcp-plaza/keys", "label": "MCP Key", "perm": "mcp_keys:view"},
    {"path": "/mcp-plaza/calls", "label": "MCP 调用记录", "perm": "mcp_calls:view"},
)

BUILTIN_PATHS: tuple[str, ...] = ("/", "/accounts", "/keys", "/logs", "/skills")

_BY_PATH = {item["path"]: item for item in NAV_TAB_CATALOG}
_MCP_PLAZA_GRANTS = (
    "mcp_knowledge:view",
    "mcp_docparse:view",
    "mcp_sites:view",
    "mcp_douyin:view",
    "mcp_browser:view",
    "mcp_portraits:view",
    "mcp_poses:view",
    "mcp_costumes:view",
    "mcp_diagrams:view",
    "generation:settings",
    "mcp_info:view",
    "mcp_keys:view",
    "mcp_calls:view",
)


def can_view(permissions: set[str], perm: str) -> bool:
    if not perm or WILDCARD in permissions:
        return True
    if perm in permissions:
        return True
    resource = perm.split(":", 1)[0]
    if f"{resource}:*" in permissions:
        return True
    if perm == "mcp_catalog:view":
        return any(can_view(permissions, code) for code in _MCP_PLAZA_GRANTS)
    return False


def _load_paths(raw: str | None) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str)]


def _row(db: Session, scope: str, admin_id: int | None) -> NavTabPreference | None:
    statement = select(NavTabPreference).where(NavTabPreference.scope == scope)
    if admin_id is None:
        statement = statement.where(NavTabPreference.admin_id.is_(None))
    else:
        statement = statement.where(NavTabPreference.admin_id == admin_id)
    return db.scalar(statement)


def stored_default_paths(db: Session) -> list[str] | None:
    row = _row(db, SCOPE_DEFAULT, None)
    if row is None:
        return None
    return _load_paths(row.paths_json)


def stored_custom_paths(db: Session, admin_id: int) -> list[str] | None:
    row = _row(db, SCOPE_USER, admin_id)
    if row is None:
        return None
    return _load_paths(row.paths_json)


def filter_paths(paths: list[str], permissions: set[str]) -> list[str]:
    visible: list[str] = []
    for path in paths:
        item = _BY_PATH.get(path)
        if item is None or not can_view(permissions, item["perm"]):
            continue
        if path not in visible:
            visible.append(path)
        if len(visible) >= MAX_TABS:
            break
    return visible


def normalize_paths(paths: list[str], *, permissions: set[str] | None = None) -> list[str]:
    if not isinstance(paths, list):
        raise HTTPException(status_code=400, detail="底部 Tab 必须是路径列表")
    cleaned: list[str] = []
    for raw in paths:
        if not isinstance(raw, str):
            raise HTTPException(status_code=400, detail="底部 Tab 包含非法路径")
        path = raw.strip()
        if not path or path in cleaned:
            continue
        item = _BY_PATH.get(path)
        if item is None:
            raise HTTPException(status_code=400, detail=f"未知页面：{path}")
        if permissions is not None and not can_view(permissions, item["perm"]):
            raise HTTPException(status_code=400, detail=f"无权限访问：{item['label']}")
        cleaned.append(path)
    if not cleaned:
        raise HTTPException(status_code=400, detail="至少选择 1 个底部 Tab")
    if len(cleaned) > MAX_TABS:
        raise HTTPException(status_code=400, detail=f"底部 Tab 最多 {MAX_TABS} 项")
    return cleaned


def _save(db: Session, scope: str, admin_id: int | None, paths: list[str]) -> None:
    row = _row(db, scope, admin_id)
    if row is None:
        row = NavTabPreference(scope=scope, admin_id=admin_id, paths_json="[]")
        db.add(row)
    row.paths_json = json.dumps(paths, ensure_ascii=False)
    row.updated_at = utcnow()
    db.commit()


def save_custom(db: Session, admin: Admin, paths: list[str]) -> list[str]:
    cleaned = normalize_paths(paths, permissions=permissions_for_admin(db, admin))
    _save(db, SCOPE_USER, admin.id, cleaned)
    return cleaned


def clear_custom(db: Session, admin_id: int) -> None:
    row = _row(db, SCOPE_USER, admin_id)
    if row is not None:
        db.delete(row)
        db.commit()


def save_default(db: Session, paths: list[str]) -> list[str]:
    cleaned = normalize_paths(paths)
    _save(db, SCOPE_DEFAULT, None, cleaned)
    return cleaned


def home_path(paths: list[str], permissions: set[str]) -> str | None:
    visible = filter_paths(paths, permissions)
    return visible[0] if visible else None


def resolve_home(db: Session, admin: Admin) -> str | None:
    permissions = permissions_for_admin(db, admin)
    custom = stored_custom_paths(db, admin.id)
    if custom:
        found = home_path(custom, permissions)
        if found:
            return found
    stored_default = stored_default_paths(db)
    return home_path(stored_default if stored_default is not None else list(BUILTIN_PATHS), permissions)


def resolve_for_admin(db: Session, admin: Admin) -> dict[str, object]:
    permissions = permissions_for_admin(db, admin)
    custom = stored_custom_paths(db, admin.id)
    stored_default = stored_default_paths(db)
    default_paths = filter_paths(stored_default if stored_default is not None else list(BUILTIN_PATHS), permissions)
    if custom is not None:
        source = "custom"
        effective = filter_paths(custom, permissions)
    elif stored_default is not None:
        source = "default"
        effective = default_paths
    else:
        source = "builtin"
        effective = default_paths
    catalog = [
        {"path": item["path"], "label": item["label"], "perm": item["perm"]}
        for item in NAV_TAB_CATALOG
        if can_view(permissions, item["perm"])
    ]
    return {
        "catalog": catalog,
        "default_paths": default_paths,
        "custom_paths": custom,
        "effective_paths": effective,
        "source": source,
        "max_tabs": MAX_TABS,
        "home_path": effective[0] if effective else None,
    }

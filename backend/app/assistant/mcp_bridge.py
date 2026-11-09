"""内置 AI 助手访问 MCP 广场的桥接层。

助手不要求用户配置 MCP Key：按管理员权限自动维护一把隐藏的 MCP Key（MCP Key 列表里
不可见），并把它作为能力调用的主体。权限边界有两层：

1. 助手工具层按 `CAPABILITY_VIEW_PERMISSIONS` 过滤，管理员看不到 / 调不到无权限的能力；
2. 隐藏 Key 的能力白名单每次按管理员当前权限同步，即使绕过工具层也无法越权。

调用仍然走 `invoke_capability`，因此调用审计、参数校验、任务归属与普通 MCP Key 一致。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.capabilities import (
    ensure_defaults,
    get_provider,
    invoke_tool,
    list_specs,
    list_tool_defs,
    resolve_tool,
)
from app.capabilities.base import CapabilityError, McpToolDef
from app.models import Admin, AssistantIdentity, McpKey
from app.permissions import CAPABILITY_VIEW_PERMISSIONS
from app.services.mcp_auth import create_mcp_key_record, replace_mcp_key_capabilities
from app.services.mcp_logs import record_mcp_call
from app.services.permission_guard import has_permission, permissions_for_admin

ASSISTANT_KEY_NAME = "内置 AI 助手"

# 明确只读的工具；未列出但以只读后缀结尾的也按只读处理
_READ_ONLY_EXACT = frozenset(
    {
        "diagram_get_source",
        "docparse_job",
        "docparse_result",
        "douyin_job",
        "douyin_result",
        "image_job",
        "image_result",
        "video_job",
        "video_result",
    }
)
_READ_ONLY_SUFFIXES = (
    "_search",
    "_get",
    "_random",
    "_list",
    "_status",
    "_schema",
    "_models",
    "_result",
    "_config",
    "_access_info",
)
# 创建类写操作：直接执行，不需要审批（生成 / 解析 / 部署 / 上报）
_DIRECT_WRITE = frozenset(
    {
        "diagram_create",
        "docparse_convert",
        "douyin_parse",
        "image_generate",
        "info_report",
        "info_report_batch",
        "site_deploy",
        "video_generate",
    }
)


def tool_access(tool_name: str) -> str:
    """read：只读；write：直接执行的创建类写操作；approval：需要用户确认的破坏性操作。"""

    if tool_name in _READ_ONLY_EXACT or tool_name.endswith(_READ_ONLY_SUFFIXES):
        return "read"
    if tool_name in _DIRECT_WRITE:
        return "write"
    return "approval"


def capability_permits(permissions: set[str], capability_id: str) -> bool:
    codes = CAPABILITY_VIEW_PERMISSIONS.get(capability_id, ())
    return bool(codes) and any(has_permission(permissions, code) for code in codes)


def authorized_tool_defs(permissions: set[str]) -> list[tuple[str, McpToolDef]]:
    ensure_defaults()
    return [
        (capability_id, tool)
        for capability_id, tool in list_tool_defs(only_enabled=True)
        if capability_permits(permissions, capability_id)
    ]


def find_authorized_tool(permissions: set[str], tool_name: str) -> tuple[str, McpToolDef] | None:
    for capability_id, tool in authorized_tool_defs(permissions):
        if tool.name == tool_name:
            return capability_id, tool
    return None


def search_tools(
    permissions: set[str], query: str, capability: str = "", limit: int = 8
) -> list[dict[str, Any]]:
    tokens = [
        token
        for token in str(query or "")
        .replace("，", " ")
        .replace(",", " ")
        .replace("、", " ")
        .split()
        if token
    ]
    capability = str(capability or "").strip()
    items: list[dict[str, Any]] = []
    for capability_id, tool in authorized_tool_defs(permissions):
        if capability and capability_id != capability:
            continue
        provider = get_provider(capability_id)
        capability_name = provider.spec.name if provider is not None else capability_id
        haystack = " ".join(
            [capability_id, capability_name, tool.name, tool.resolved_operation(), tool.description]
        ).lower()
        if tokens and not all(token.lower() in haystack for token in tokens):
            continue
        items.append(
            {
                "tool": tool.name,
                "capability": capability_id,
                "capability_name": capability_name,
                "operation": tool.resolved_operation(),
                "description": tool.description[:240],
                "access": tool_access(tool.name),
            }
        )
        if len(items) >= max(1, min(int(limit or 8), 20)):
            break
    return items


def describe_tools(permissions: set[str], names: list[str]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in names[:5]:
        found = find_authorized_tool(permissions, name)
        if found is None:
            missing.append(name)
            continue
        capability_id, tool = found
        provider = get_provider(capability_id)
        items.append(
            {
                "tool": tool.name,
                "capability": capability_id,
                "capability_name": provider.spec.name if provider is not None else capability_id,
                "operation": tool.resolved_operation(),
                "description": tool.description,
                "input_schema": tool.input_schema,
                "returns": tool.returns,
                "access": tool_access(tool.name),
            }
        )
    return {"items": items, "missing": missing}


def _capability_ids(permissions: set[str]) -> list[str]:
    ensure_defaults()
    return [
        spec.capability_id
        for spec in list_specs(only_enabled=True)
        if capability_permits(permissions, spec.capability_id)
    ]


def _load_key(db: Session, key_id: int | None) -> McpKey | None:
    if key_id is None:
        return None
    return db.scalar(select(McpKey).where(McpKey.id == key_id).options(selectinload(McpKey.capabilities)))


def get_assistant_key(db: Session, admin_id: int, permissions: set[str], *, attempts: int = 2) -> McpKey:
    """取当前管理员的隐藏助手 Key；能力白名单按当前权限同步。"""

    desired = set(_capability_ids(permissions))
    if not desired:
        raise CapabilityError(
            "当前账号没有可用的 MCP 能力权限", status_code=403, error_type="permission_error"
        )
    identity = db.scalar(select(AssistantIdentity).where(AssistantIdentity.admin_id == admin_id))
    key = _load_key(db, identity.mcp_key_id) if identity is not None else None
    if key is None:
        try:
            key, _plaintext = create_mcp_key_record(db, ASSISTANT_KEY_NAME, sorted(desired))
            key.status = "active"
            if identity is None:
                db.add(AssistantIdentity(admin_id=admin_id, mcp_key_id=key.id))
            else:
                identity.mcp_key_id = key.id
            db.flush()
        except IntegrityError:
            db.rollback()
            if attempts > 1:
                return get_assistant_key(db, admin_id, permissions, attempts=attempts - 1)
            raise
        return _load_key(db, key.id) or key

    if key.status != "active":
        key.status = "active"
    existing = {item.capability_id for item in key.capabilities}
    if existing != desired:
        replace_mcp_key_capabilities(db, key, sorted(desired))
        db.flush()
        key = _load_key(db, key.id) or key
    return key


def _record_failure(
    db: Session,
    key: McpKey,
    tool_name: str,
    payload: dict[str, Any],
    error: CapabilityError,
    started: float,
) -> None:
    resolved = resolve_tool(tool_name)
    if resolved is None:
        return
    capability_id, operation = resolved
    try:
        record_mcp_call(
            db,
            mcp_key=key,
            capability_id=capability_id,
            operation=operation,
            success=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error_message=error.message,
            error_type=error.error_type,
            http_status=error.status_code,
            request_body=payload,
        )
        db.commit()
    except Exception:  # noqa: BLE001 - 审计写入失败不能覆盖原始错误
        db.rollback()


async def invoke_as_assistant(
    db: Session,
    *,
    admin_id: int,
    permissions: set[str],
    tool_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    key = get_assistant_key(db, admin_id, permissions)
    # 先落库隐藏 Key：调用失败回滚时不能把 Key 一起回滚掉，否则后续审计会找不到主体
    db.commit()
    try:
        result = await invoke_tool(db, mcp_key=key, tool_name=tool_name, payload=payload, system=True)
        db.commit()
        return result
    except CapabilityError as error:
        db.rollback()
        _record_failure(db, key, tool_name, payload, error, started)
        raise
    except Exception as error:  # noqa: BLE001
        db.rollback()
        normalized = CapabilityError(str(error), status_code=500, error_type="internal_error")
        _record_failure(db, key, tool_name, payload, normalized, started)
        raise normalized from error


async def invoke_for_admin(
    db: Session,
    *,
    admin: Admin,
    tool_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """审批通过后再校验一次权限并执行，避免审批期间角色被降权。"""

    permissions = permissions_for_admin(db, admin)
    if find_authorized_tool(permissions, tool_name) is None:
        raise CapabilityError(
            "当前账号没有权限调用该 MCP 工具", status_code=403, error_type="permission_error"
        )
    return await invoke_as_assistant(
        db,
        admin_id=int(admin.id),
        permissions=permissions,
        tool_name=tool_name,
        payload=payload,
    )

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy.exc import OperationalError

from app.assistant.api_catalog import (
    ApiOperation,
    build_catalog,
    catalog_payload,
    describe_operation,
    get_operation,
    normalize_navigation_path,
    page_directory,
    search_operations,
)
from app.assistant.api_client import execute_api, preview_request, redact_value
from app.assistant.store import approval_payload, create_approval
from app.db import get_session_factory
from app.services.permission_guard import has_permission, required_codes

API_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "api_search",
        "description": (
            "搜索站点后台接口。按用户意图传入简短关键词（如 抖音、站点、生成、知识库、日志），"
            "返回接口 id、方法、路径、说明、是否只读、是否需要审批。"
            "需要调用接口时先搜索，再 api_describe 看参数，最后 api_call。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，支持中文或英文"},
                "category": {"type": "string", "description": "可选分类，如 douyin / sites / generation"},
                "read_only": {"type": "boolean", "description": "只返回只读接口"},
                "limit": {"type": "integer", "description": "返回数量，1-20，默认 8", "default": 8},
            },
            "required": ["query"],
        },
    },
}

API_DESCRIBE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "api_describe",
        "description": (
            "读取接口的完整参数、请求体 schema 和示例。参数：api_ids 是 api_search 返回的 id 数组，最多 5 个。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "api_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "api_search 返回的接口 id，如 GET /api/admin/mcp/douyin/jobs",
                }
            },
            "required": ["api_ids"],
        },
    },
}

API_CALL_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "api_call",
        "description": (
            "调用站点接口。GET 等只读接口直接执行；POST/PUT/PATCH/DELETE 会先创建审批，"
            "由用户确认后才执行。路径参数放 path_params，查询参数放 query，JSON 请求体放 body。"
            "写操作请填写 reason，说明为什么要执行，方便用户审批。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "api_id": {"type": "string", "description": "api_search 返回的接口 id"},
                "path_params": {"type": "object", "description": "路径参数，如 {\"site_id\": \"abc\"}"},
                "query": {"type": "object", "description": "查询参数"},
                "body": {"type": "object", "description": "JSON 请求体"},
                "reason": {"type": "string", "description": "写操作给用户看的执行原因"},
            },
            "required": ["api_id"],
        },
    },
}

UI_NAVIGATE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ui_navigate",
        "description": (
            "在管理后台打开一个页面。用户说「打开 MCP 广场」「带我去抖音下载」时调用。"
            "只能跳转站内管理页面，path 必须以 / 开头。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "站内路径，优先从系统提示中的可用页面目录选择"},
                "title": {"type": "string", "description": "可选页面名称，用于提示"},
            },
            "required": ["path"],
        },
    },
}

TOOL_DEFS = [API_SEARCH_TOOL, API_DESCRIBE_TOOL, API_CALL_TOOL, UI_NAVIGATE_TOOL]

_DROP_KEYS = frozenset(
    {
        "content_base64",
        "archive_base64",
        "image_base64",
        "file_base64",
        "api_key",
        "api_key_encrypted",
        "password",
        "secret",
        "access_token",
        "refresh_token",
    }
)
_MAX_STRING = 1200
_MAX_LIST = 8
_MAX_RESULT_CHARS = 20000
_MAX_LLM_CHARS = 8000


def build_tool_defs() -> list[dict[str, Any]]:
    return [dict(tool) for tool in TOOL_DEFS]


def _compact(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "…"
    if isinstance(value, str):
        if len(value) > _MAX_STRING:
            return value[:_MAX_STRING] + f"…[截断 {len(value) - _MAX_STRING} 字]"
        return value
    if isinstance(value, list):
        items = [_compact(item, depth + 1) for item in value[:_MAX_LIST]]
        if len(value) > _MAX_LIST:
            items.append(f"…共 {len(value)} 项")
        return items
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in _DROP_KEYS:
                result[key] = "***"
                continue
            result[str(key)] = _compact(item, depth + 1)
        return result
    return value


def compact_result(result: Any) -> Any:
    return _compact(result)


def result_json(result: Any) -> str:
    raw = json.dumps(compact_result(result), ensure_ascii=False, default=str)
    if len(raw) > _MAX_RESULT_CHARS:
        return raw[:_MAX_RESULT_CHARS] + "…[结果过大已截断]"
    return raw


def llm_result(result: Any) -> str:
    raw = result_json(result)
    if len(raw) > _MAX_LLM_CHARS:
        return raw[:_MAX_LLM_CHARS] + "…[结果过大已截断]"
    return raw


def _allowed(operation: ApiOperation, permissions: set[str]) -> bool:
    codes = required_codes(operation.method, operation.path)
    if not codes:
        return True
    return any(has_permission(permissions, code) for code in codes)


def _summary_from_result(operation: ApiOperation, outcome: dict[str, Any]) -> str:
    if not outcome.get("ok"):
        return f"{operation.summary}失败：{outcome.get('error') or '未知错误'}"
    data = outcome.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            return f"{operation.summary} · {len(data['items'])} 项"
        if isinstance(data.get("total"), int):
            return f"{operation.summary} · 共 {data['total']} 条"
        for key in ("id", "job_id", "site_id", "key_id"):
            if data.get(key):
                return f"{operation.summary} · {data[key]}"
    return operation.summary


def _missing_required(operation: ApiOperation, query: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for parameter in operation.parameters:
        if not parameter.get("required") or parameter.get("in") not in {"path", "query"}:
            continue
        name = str(parameter.get("name") or "")
        if not name:
            continue
        if parameter.get("in") == "path":
            continue
        value = query.get(name)
        if value is None or value == "":
            missing.append(name)
    return missing


def _path_missing(operation: ApiOperation, path_params: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for parameter in operation.parameters:
        if parameter.get("in") != "path":
            continue
        name = str(parameter.get("name") or "")
        if not name:
            continue
        value = path_params.get(name)
        if value is None or value == "":
            missing.append(name)
    return missing


def _create_approval_sync(
    *,
    conversation_id: str,
    admin_id: int,
    tool_call_id: str,
    operation: ApiOperation,
    path_params: dict[str, Any],
    query: dict[str, Any],
    body: Any,
    reason: str,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(3):
        session = get_session_factory()()
        try:
            row = create_approval(
                session,
                conversation_id=conversation_id,
                admin_id=admin_id,
                tool_call_id=tool_call_id,
                operation=operation,
                path_params=path_params,
                query=query,
                body=body,
                reason=reason,
            )
            session.commit()
            return approval_payload(row)
        except OperationalError as error:
            session.rollback()
            last_error = error
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
        finally:
            session.close()
    raise last_error or RuntimeError("创建审批失败")


def _error(name: str, message: str) -> dict[str, Any]:
    return {
        "kind": "result",
        "ok": False,
        "result": None,
        "error": message,
        "summary": f"{name}失败：{message}",
        "latency_ms": 0,
        "approval": None,
        "navigate": None,
    }


async def execute_tool(
    *,
    app,
    token: str,
    permissions: set[str],
    admin_id: int,
    conversation_id: str,
    tool_call_id: str,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()

    if name == "api_search":
        query = str(arguments.get("query") or "").strip()
        if not query:
            return _error(name, "请提供搜索关键词")
        category = str(arguments.get("category") or "").strip()
        read_only_raw = arguments.get("read_only")
        read_only = bool(read_only_raw) if isinstance(read_only_raw, bool) else None
        try:
            limit = int(arguments.get("limit") or 8)
        except (TypeError, ValueError):
            limit = 8
        operations = [item for item in build_catalog(app) if _allowed(item, permissions)]
        matches = search_operations(
            operations,
            query,
            category=category,
            read_only=read_only,
            limit=limit,
        )
        items = [catalog_payload(item) for item in matches]
        result = {
            "query": query,
            "items": items,
            "total": len(items),
            "hint": "" if items else "没有匹配的接口，换更短的关键词，或先用中文描述功能。",
        }
        return {
            "kind": "result",
            "ok": True,
            "result": result,
            "error": "",
            "summary": f"找到 {len(items)} 个接口",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "approval": None,
            "navigate": None,
        }

    if name == "api_describe":
        raw_ids = arguments.get("api_ids")
        if isinstance(raw_ids, str):
            ids = [item.strip() for item in raw_ids.split(",") if item.strip()]
        elif isinstance(raw_ids, list):
            ids = [str(item).strip() for item in raw_ids if str(item).strip()]
        else:
            ids = []
        if not ids:
            return _error(name, "请提供 api_ids")
        items: list[dict[str, Any]] = []
        missing: list[str] = []
        for api_id in ids[:5]:
            operation = get_operation(app, api_id)
            if operation is None or not _allowed(operation, permissions):
                missing.append(api_id)
                continue
            items.append(describe_operation(operation))
        result = {"items": items, "missing": missing}
        return {
            "kind": "result",
            "ok": bool(items),
            "result": result,
            "error": "" if items else "没有找到可用接口",
            "summary": f"读取 {len(items)} 个接口定义",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "approval": None,
            "navigate": None,
        }

    if name == "ui_navigate":
        path = normalize_navigation_path(str(arguments.get("path") or ""), permissions)
        if path is None:
            pages = "、".join(f"{item['label']} {item['path']}" for item in page_directory(permissions))
            message = f"当前账号没有权限打开该页面。可用页面：{pages}" if pages else "当前账号没有权限打开管理页面"
            return _error(name, message)
        title = str(arguments.get("title") or "").strip()
        return {
            "kind": "navigate",
            "ok": True,
            "result": {"path": path, "title": title},
            "error": "",
            "summary": f"打开页面 {title or path}",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "approval": None,
            "navigate": path,
        }

    if name == "api_call":
        api_id = str(arguments.get("api_id") or "").strip()
        operation = get_operation(app, api_id)
        if operation is None:
            return _error(name, f"未知接口 id：{api_id}。先用 api_search 搜索。")
        if not _allowed(operation, permissions):
            return _error(name, f"当前账号没有权限调用 {operation.id}")
        if not operation.supported:
            return _error(
                name,
                f"{operation.summary} 需要文件或表单上传，请在对应页面操作；可以用 ui_navigate 打开页面。",
            )
        path_params = arguments.get("path_params")
        query = arguments.get("query")
        body = arguments.get("body")
        path_params = path_params if isinstance(path_params, dict) else {}
        query = query if isinstance(query, dict) else {}
        missing_path = _path_missing(operation, path_params)
        if missing_path:
            return _error(name, f"缺少路径参数：{', '.join(missing_path)}")
        missing_query = _missing_required(operation, query)
        if missing_query:
            return _error(name, f"缺少必填查询参数：{', '.join(missing_query)}")
        if operation.body_required and body is None:
            return _error(name, "该接口需要 JSON 请求体，请先用 api_describe 查看 body_example")
        if body is not None and not isinstance(body, dict | list):
            return _error(name, "body 必须是 JSON 对象或数组")
        reason = str(arguments.get("reason") or "").strip() or operation.summary

        if operation.read_only:
            outcome = await execute_api(
                app,
                token,
                operation,
                path_params=path_params,
                query=query,
                body=body,
                timeout_seconds=60.0,
            )
            return {
                "kind": "result",
                "ok": bool(outcome.get("ok")),
                "result": compact_result(outcome.get("data")) if outcome.get("ok") else None,
                "error": str(outcome.get("error") or ""),
                "summary": _summary_from_result(operation, outcome),
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "approval": None,
                "navigate": None,
                "status": outcome.get("status"),
            }

        preview = preview_request(operation, path_params, query, body)
        try:
            approval = _create_approval_sync(
                conversation_id=conversation_id,
                admin_id=admin_id,
                tool_call_id=tool_call_id,
                operation=operation,
                path_params=path_params,
                query=query,
                body=body,
                reason=reason,
            )
        except Exception as error:  # noqa: BLE001
            return _error(name, f"创建审批失败：{error}")
        approval["preview"] = preview
        return {
            "kind": "approval",
            "ok": True,
            "result": None,
            "error": "",
            "summary": f"等待确认：{operation.method} {preview['path']}",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "approval": approval,
            "navigate": None,
        }

    return _error(name, f"未知工具：{name}")


def approval_preview(row) -> dict[str, Any]:
    payload = approval_payload(row)
    data = payload.get("body")
    return {
        "method": payload["method"],
        "path": payload["path"],
        "query": redact_value(payload.get("query") or {}),
        "body": redact_value(data) if data is not None else None,
    }

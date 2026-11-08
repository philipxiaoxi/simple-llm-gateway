from __future__ import annotations

import json
import re
import warnings
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

from app.services.permission_guard import has_permission, resolve_permission

_CATALOG_APP_ID: int | None = None
_CATALOG: list[ApiOperation] | None = None

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

BLOCKED_PREFIXES = (
    "/api/admin/assistant",
    "/api/admin/oauth",
)
BLOCKED_PATHS = frozenset(
    {
        "/api/admin/login",
        "/api/admin/accounts/export",
        "/api/admin/keys/{key_id}/reveal",
        "/api/admin/keys/{key_id}/cc-switch",
        "/api/admin/mcp/sites/{site_id}/token",
        "/api/admin/mcp/diagrams/{site_id}/token",
        "/api/admin/tools/{tool_id}/events",
        "/api/admin/voice/rooms/{room_id}/events",
        "/api/admin/voice/rooms/{room_id}/live",
    }
)
BLOCKED_SUFFIXES = ("/download",)

CATEGORY_RULES: tuple[tuple[str, str, str], ...] = (
    ("/api/admin/mcp/douyin", "douyin", "抖音下载"),
    ("/api/admin/mcp/knowledge", "knowledge", "知识库"),
    ("/api/admin/mcp/docparse", "docparse", "文档解析"),
    ("/api/admin/mcp/sites", "sites", "站点部署"),
    ("/api/admin/mcp/portraits", "portraits", "人像参考库"),
    ("/api/admin/mcp/poses", "poses", "姿势参考库"),
    ("/api/admin/mcp/costumes", "costumes", "服装参考库"),
    ("/api/admin/mcp/handdraws", "handdraws", "手绘风格库"),
    ("/api/admin/mcp/diagrams", "diagrams", "图表"),
    ("/api/admin/mcp/info-reports", "info", "资讯上报"),
    ("/api/admin/mcp/calls", "mcp_calls", "MCP 调用记录"),
    ("/api/admin/mcp/keys", "mcp_keys", "MCP Key"),
    ("/api/admin/mcp", "mcp", "MCP 广场"),
    ("/api/admin/generation", "generation", "图片/视频生成"),
    ("/api/admin/info", "info", "资讯收集"),
    ("/api/admin/accounts", "accounts", "上游账号"),
    ("/api/admin/keys", "keys", "API Key"),
    ("/api/admin/logs", "logs", "记录审计"),
    ("/api/admin/dashboard", "dashboard", "概览"),
    ("/api/admin/benchmark/history", "benchmark_history", "测速历史"),
    ("/api/admin/benchmark/groups", "benchmark_groups", "测速分组"),
    ("/api/admin/benchmark", "benchmark", "模型测速"),
    ("/api/admin/leaderboard", "leaderboard", "模型榜"),
    ("/api/admin/agents", "agents", "网关代理"),
    ("/api/admin/offline", "offline", "离线下载"),
    ("/api/admin/skills", "skills", "Skills"),
    ("/api/admin/skill-bundles", "skill_bundles", "Skills 组合包"),
    ("/api/admin/security", "security", "安全中心"),
    ("/api/admin/tools", "tools", "工具中心"),
    ("/api/admin/jobs", "jobs", "定时任务"),
    ("/api/admin/voice", "voice", "语音房"),
    ("/api/admin/users", "users", "用户管理"),
    ("/api/admin/roles", "roles", "角色管理"),
    ("/api/admin/integrations", "integrations", "第三方集成"),
    ("/api/admin/public-gate", "public_gate", "公开门禁"),
    ("/api/admin/permission-catalog", "roles", "权限目录"),
    ("/api/admin/settings", "settings", "站点设置"),
    ("/api/admin/me", "me", "当前账号"),
)

CATEGORY_NOUNS = {
    "角色管理": "角色",
    "用户管理": "用户",
    "记录审计": "请求日志",
    "资讯收集": "资讯",
    "图片/视频生成": "生成任务",
    "站点部署": "站点",
    "抖音下载": "抖音下载任务",
    "模型测速": "测速任务",
    "测速历史": "测速记录",
    "安全中心": "安全事件",
    "工具中心": "桌面工具",
    "文档解析": "文档转换任务",
    "权限目录": "权限",
    "当前账号": "当前账号",
}

ACTION_LABELS = {
    "collect": "采集",
    "collect-batch": "批量采集",
    "preview": "预览",
    "retry": "重试",
    "cancel": "取消",
    "refresh": "刷新",
    "probe": "连通性测试",
    "quota": "刷新额度",
    "export": "导出",
    "import": "导入",
    "search": "搜索",
    "share": "生成分享链接",
    "rollback": "回滚",
    "activate": "启用版本",
    "reindex": "重建索引",
    "reembed": "重新向量化",
    "ingest": "入库",
    "clone": "复制",
    "upload": "上传",
    "optimize": "优化",
    "rescore": "重新评分",
    "run": "执行",
    "stop": "停止",
    "test": "测试",
    "token": "生成访问令牌",
    "batch-interval": "批量设置间隔",
    "ai-skip": "AI 跳过",
    "favorite": "收藏",
    "featured": "推荐",
    "hidden": "隐藏",
    "polish": "润色",
}

DESCRIPTION_OVERRIDES: dict[tuple[str, str], str] = {
    ("GET", "/api/admin/mcp/douyin/jobs"): "列出抖音下载任务，可按状态、关键词筛选，返回任务进度与媒体数量",
    ("POST", "/api/admin/mcp/douyin/jobs"): "提交抖音分享链接或直链，创建下载/转存任务；返回 job_id，可在任务详情查看进度",
    ("GET", "/api/admin/mcp/douyin/jobs/{job_id}"): "查看抖音下载任务详情，包含进度、作品信息与媒体下载地址",
    ("GET", "/api/admin/mcp/douyin/provider"): "查看抖音解析 API（TikHub）的配置状态与 Base URL",
    ("PUT", "/api/admin/mcp/douyin/provider"): "保存抖音解析 API（TikHub）的 Base URL 与密钥",
    ("DELETE", "/api/admin/mcp/douyin/provider"): "清除抖音解析 API 配置",
    ("GET", "/api/admin/integrations/tikhub"): "查看 TikHub 解析服务的集成配置",
    ("PUT", "/api/admin/integrations/tikhub"): "保存 TikHub 解析服务的 Base URL 与密钥",
    ("DELETE", "/api/admin/integrations/tikhub"): "清除 TikHub 解析服务配置",
    ("POST", "/api/admin/mcp/douyin/jobs/{job_id}/retry"): "重试失败或部分完成的抖音下载任务",
    ("DELETE", "/api/admin/mcp/douyin/jobs/{job_id}"): "删除抖音下载任务及其媒体文件",
    ("GET", "/api/admin/mcp/sites"): "列出已部署的静态站点，返回站点名称、访问地址、版本与状态",
    ("POST", "/api/admin/mcp/sites"): "上传前端静态资源并部署为预览站点",
    ("GET", "/api/admin/mcp/sites/{site_id}"): "查看站点详情、版本列表与访问地址",
    ("POST", "/api/admin/mcp/sites/{site_id}/versions"): "为站点创建新版本并部署",
    ("GET", "/api/admin/mcp/diagrams"): "列出图表站点，返回标题、类型与访问地址",
    ("POST", "/api/admin/mcp/diagrams"): "提交图表 JSON 源并发布为可访问的图表站点；创建前先读取 diagram_schema 规范",
    ("GET", "/api/admin/mcp/diagrams/{site_id}"): "查看图表详情、版本与访问地址",
    ("POST", "/api/admin/mcp/diagrams/{site_id}/rollback"): "把图表回滚到指定历史版本",
    ("GET", "/api/admin/mcp/knowledge/bases"): "列出知识库，返回名称、文档数量与 embedding 配置",
    ("POST", "/api/admin/mcp/knowledge/bases/{kb_id}/search"): "在知识库中检索相关文本块，返回命中片段与来源",
    ("GET", "/api/admin/mcp/docparse/jobs"): "列出文档转 Markdown 任务",
    ("POST", "/api/admin/mcp/docparse/jobs"): "提交文档并转换为 Markdown；文件上传请使用对应管理页面",
    ("GET", "/api/admin/generation/tasks"): "列出图片/视频生成任务，可按类型、状态筛选",
    ("POST", "/api/admin/generation/tasks"): "提交图片或视频生成任务，返回任务 id；生成结果在任务详情查看",
    ("GET", "/api/admin/generation/settings"): "查看图片/视频生成供应商与默认模型配置",
    ("GET", "/api/admin/info/items"): "列出资讯条目，可按状态、来源、关键词筛选",
    ("GET", "/api/admin/generation/tasks/{task_id}"): "查看图片/视频生成任务详情、进度与结果文件",
    ("GET", "/api/admin/accounts/{account_id}"): "查看上游账号详情、模型目录与最近探测结果",
    ("GET", "/api/admin/logs/{log_id}"): "查看单条网关请求日志的摘要与用量",
    ("GET", "/api/admin/info/sources"): "列出资讯采集源",
    ("POST", "/api/admin/info/sources/collect-batch"): "批量触发资讯源采集",
    ("GET", "/api/admin/accounts"): "列出现有上游账号，特别关心的账号排在前面，返回名称、供应商、状态、额度与模型目录",
    ("POST", "/api/admin/accounts"): "新增上游账号；需要 provider、name、base_url、api_key 等字段",
    ("GET", "/api/admin/keys"): "列出网关 API Key，返回名称、前缀、状态与额度",
    ("POST", "/api/admin/keys"): "创建网关 API Key，返回明文密钥；明文只在创建时出现一次",
    ("GET", "/api/admin/logs"): "列出网关请求日志，可按模型、账号、状态、时间筛选",
    ("GET", "/api/admin/dashboard"): "读取概览页的调用量、Token、成功率与平均耗时",
    ("GET", "/api/admin/roles"): "列出角色及其权限码",
    ("GET", "/api/admin/users"): "列出管理后台用户与角色",
    ("GET", "/api/admin/security/events"): "列出安全中心事件",
    ("GET", "/api/admin/tools"): "列出工具中心的桌面工具与执行状态",
    ("GET", "/api/admin/jobs"): "列出定时任务及其最近执行状态",
    ("GET", "/api/admin/voice/rooms"): "列出语音房与在线状态",
}

PAGE_DIRECTORY: tuple[dict[str, str], ...] = (
    {"path": "/", "label": "概览", "perm": "dashboard:view"},
    {"path": "/accounts", "label": "上游账号", "perm": "accounts:view"},
    {"path": "/agents", "label": "网关代理", "perm": "agents:view"},
    {"path": "/keys", "label": "API Key", "perm": "keys:view"},
    {"path": "/logs", "label": "记录审计", "perm": "logs:view"},
    {"path": "/benchmark", "label": "模型测速", "perm": "benchmark:view"},
    {"path": "/benchmark/history", "label": "测速历史", "perm": "benchmark_history:view"},
    {"path": "/leaderboard", "label": "模型榜", "perm": "leaderboard:view"},
    {"path": "/info", "label": "资讯收集", "perm": "info:view"},
    {"path": "/info/sources", "label": "资讯源", "perm": "info:view"},
    {"path": "/image-gen", "label": "图片生成", "perm": "image_gen:view"},
    {"path": "/video-gen", "label": "视频生成", "perm": "video_gen:view"},
    {"path": "/offline", "label": "离线下载", "perm": "offline:view"},
    {"path": "/skills", "label": "Skills", "perm": "skills:view"},
    {"path": "/skills/bundles", "label": "Skills 组合包", "perm": "skill_bundles:view"},
    {"path": "/mcp-plaza", "label": "MCP 广场", "perm": "mcp_catalog:view"},
    {"path": "/mcp-plaza/knowledge", "label": "知识库", "perm": "mcp_knowledge:view"},
    {"path": "/mcp-plaza/docparse", "label": "文档解析", "perm": "mcp_docparse:view"},
    {"path": "/mcp-plaza/sites", "label": "站点部署", "perm": "mcp_sites:view"},
    {"path": "/mcp-plaza/douyin", "label": "抖音下载", "perm": "mcp_douyin:view"},
    {"path": "/mcp-plaza/diagrams", "label": "图表", "perm": "mcp_diagrams:view"},
    {"path": "/mcp-plaza/info-report", "label": "资讯上报", "perm": "mcp_info:view"},
    {"path": "/mcp-plaza/portraits", "label": "人像参考库", "perm": "mcp_portraits:view"},
    {"path": "/mcp-plaza/poses", "label": "姿势参考库", "perm": "mcp_poses:view"},
    {"path": "/mcp-plaza/costumes", "label": "服装参考库", "perm": "mcp_costumes:view"},
    {"path": "/mcp-plaza/handdraws", "label": "手绘风格库", "perm": "mcp_handdraws:view"},
    {"path": "/mcp-plaza/generation", "label": "生成配置", "perm": "generation:settings"},
    {"path": "/mcp-plaza/keys", "label": "MCP Key", "perm": "mcp_keys:view"},
    {"path": "/mcp-plaza/calls", "label": "MCP 调用记录", "perm": "mcp_calls:view"},
    {"path": "/tools", "label": "工具中心", "perm": "tools:view"},
    {"path": "/jobs", "label": "定时任务", "perm": "jobs:view"},
    {"path": "/security", "label": "安全中心", "perm": "security:view"},
    {"path": "/voice", "label": "语音房", "perm": "voice:view"},
    {"path": "/users", "label": "用户管理", "perm": "users:view"},
    {"path": "/roles", "label": "角色管理", "perm": "roles:view"},
)

PAGE_DETAIL_PERMS = ((re.compile(r"^/logs/[^/]+$"), "logs:detail"),)


@dataclass
class ApiOperation:
    id: str
    method: str
    path: str
    summary: str
    description: str
    category: str
    category_id: str
    permission: str | None
    read_only: bool
    supported: bool
    parameters: list[dict[str, Any]] = field(default_factory=list)
    body_schema: dict[str, Any] | None = None
    body_required: bool = False
    tags: list[str] = field(default_factory=list)
    keywords: str = ""

    def search_text(self) -> str:
        return " ".join(
            [
                self.id,
                self.method,
                self.path,
                self.summary,
                self.description,
                self.category,
                self.category_id,
                self.permission or "",
                self.keywords,
            ]
        ).lower()


def _category(path: str) -> tuple[str, str]:
    for prefix, category_id, label in CATEGORY_RULES:
        if path == prefix or path.startswith(prefix + "/"):
            return category_id, label
    return "site", "站点功能"


def _resolve_ref(schema: Any, components: dict[str, Any], depth: int = 0) -> Any:
    if depth > 8:
        return schema
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.rsplit("/", 1)[-1]
            target = components.get(name)
            if target is not None:
                return _resolve_ref(target, components, depth + 1)
        return {key: _resolve_ref(value, components, depth + 1) for key, value in schema.items()}
    if isinstance(schema, list):
        return [_resolve_ref(item, components, depth + 1) for item in schema]
    return schema


def _simplify_schema(schema: Any, depth: int = 0) -> Any:
    if depth > 5 or not isinstance(schema, dict):
        return schema
    keep = {}
    for key in (
        "type",
        "format",
        "description",
        "default",
        "enum",
        "required",
        "properties",
        "items",
        "anyOf",
        "oneOf",
        "allOf",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "title",
    ):
        if key in schema:
            value = schema[key]
            if key == "properties" and isinstance(value, dict):
                keep[key] = {name: _simplify_schema(item, depth + 1) for name, item in value.items()}
            elif key in {"items", "anyOf", "oneOf", "allOf"}:
                if isinstance(value, list):
                    keep[key] = [_simplify_schema(item, depth + 1) for item in value]
                else:
                    keep[key] = _simplify_schema(value, depth + 1)
            else:
                keep[key] = value
    return keep


def _parameter_payload(raw: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    schema = _simplify_schema(_resolve_ref(raw.get("schema") or {}, components))
    return {
        "name": str(raw.get("name") or ""),
        "in": str(raw.get("in") or ""),
        "required": bool(raw.get("required")),
        "description": str(raw.get("description") or ""),
        "schema": schema,
    }


def _description_for(method: str, path: str, summary: str, category_label: str) -> str:
    override = DESCRIPTION_OVERRIDES.get((method, path))
    if override:
        return override
    noun = CATEGORY_NOUNS.get(category_label, category_label)
    segments = [segment for segment in path.split("/") if segment and segment not in {"api", "admin"}]
    last = segments[-1] if segments else ""
    action = ACTION_LABELS.get(last, "")
    is_item = last.startswith("{")
    if method == "GET":
        if is_item:
            return f"查看{noun}详情"
        if action:
            return f"{noun} · {action}"
        return f"列出{noun}"
    if method == "POST":
        if action:
            return f"{noun} · {action}"
        return f"创建{noun}"
    if method in {"PUT", "PATCH"}:
        return f"更新{noun}"
    if method == "DELETE":
        return f"删除{noun}"
    return summary or f"{noun} · {method}"


def _keywords_for(path: str, category_label: str) -> str:
    words = [category_label]
    for segment in path.split("/"):
        if not segment or segment.startswith("{") or segment in {"api", "admin"}:
            continue
        words.append(segment.replace("-", " ").replace("_", " "))
    return " ".join(words)


def _body_from_openapi(operation: dict[str, Any]) -> tuple[dict[str, Any] | None, bool, bool]:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return None, False, True
    content = request_body.get("content")
    if not isinstance(content, dict) or not content:
        return None, bool(request_body.get("required")), True
    if "application/json" in content:
        schema = content["application/json"].get("schema") or {}
        return schema, bool(request_body.get("required")), True
    return None, bool(request_body.get("required")), False


def _is_blocked(method: str, path: str) -> bool:
    if method not in METHODS:
        return True
    if path in BLOCKED_PATHS:
        return True
    if any(path.startswith(prefix) for prefix in BLOCKED_PREFIXES):
        return True
    return any(path.endswith(suffix) for suffix in BLOCKED_SUFFIXES)


def build_catalog(app) -> list[ApiOperation]:
    global _CATALOG, _CATALOG_APP_ID
    if _CATALOG is not None and id(app) == _CATALOG_APP_ID:
        return _CATALOG
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        schema = app.openapi()
    components = (schema.get("components") or {}).get("schemas") or {}
    seen: set[tuple[str, str]] = set()
    operations: list[ApiOperation] = []
    for path, methods in (schema.get("paths") or {}).items():
        if not isinstance(path, str) or not path.startswith("/api/admin"):
            continue
        normalized = path.rstrip("/") or path
        for method, operation in (methods or {}).items():
            upper = method.upper()
            if not isinstance(operation, dict) or _is_blocked(upper, path):
                continue
            key = (upper, normalized)
            if key in seen:
                continue
            seen.add(key)
            category_id, category_label = _category(path)
            parameters: list[dict[str, Any]] = []
            for raw in (operation.get("parameters") or []):
                if not isinstance(raw, dict):
                    continue
                if raw.get("in") == "header":
                    continue
                parameters.append(_parameter_payload(raw, components))
            body_schema, body_required, supported_body = _body_from_openapi(operation)
            resolved_body = _simplify_schema(_resolve_ref(body_schema, components)) if body_schema else None
            summary = str(operation.get("summary") or "").strip()
            description = _description_for(upper, path, summary, category_label)
            permission = resolve_permission(upper, path)
            operations.append(
                ApiOperation(
                    id=f"{upper} {path}",
                    method=upper,
                    path=path,
                    summary=description,
                    description=description,
                    category=category_label,
                    category_id=category_id,
                    permission=permission,
                    read_only=upper in READ_METHODS,
                    supported=supported_body,
                    parameters=parameters,
                    body_schema=resolved_body,
                    body_required=body_required,
                    tags=[str(tag) for tag in operation.get("tags") or []],
                    keywords=_keywords_for(path, category_label),
                )
            )
    operations.sort(key=lambda item: (item.category_id, item.path, METHODS.index(item.method)))
    _CATALOG = operations
    _CATALOG_APP_ID = id(app)
    return operations


def get_operation(app, api_id: str) -> ApiOperation | None:
    target = api_id.strip()
    for operation in build_catalog(app):
        if operation.id == target:
            return operation
    for operation in build_catalog(app):
        if operation.path == target or f"{operation.method} {operation.path}" == target:
            return operation
    return None


def _score(query: str, operation: ApiOperation) -> int:
    haystack = operation.search_text()
    lowered = query.lower().strip()
    if not lowered:
        return 1
    score = 0
    if lowered in haystack:
        score += 20
    category = operation.category.lower()
    path = operation.path.lower()
    tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", lowered)
    for token in tokens:
        if token in haystack:
            score += 8
        if len(token) >= 2:
            for index in range(len(token) - 1):
                bigram = token[index : index + 2]
                if bigram in haystack:
                    score += 2
                if bigram in category or bigram in path:
                    score += 4
    if operation.read_only:
        score += 1
    return score


def search_operations(
    operations: list[ApiOperation],
    query: str,
    *,
    category: str = "",
    read_only: bool | None = None,
    limit: int = 8,
) -> list[ApiOperation]:
    category_value = category.strip().lower()
    scored: list[tuple[int, ApiOperation]] = []
    for operation in operations:
        if category_value and category_value not in {
            operation.category_id.lower(),
            operation.category.lower(),
        }:
            continue
        if read_only is not None and operation.read_only != read_only:
            continue
        score = _score(query, operation)
        if score > 1 or not query.strip():
            scored.append((score, operation))
    scored.sort(key=lambda item: (-item[0], item[1].read_only, item[1].path, METHODS.index(item[1].method)))
    return [operation for _score_value, operation in scored[: max(1, min(limit, 20))]]


def describe_operation(operation: ApiOperation) -> dict[str, Any]:
    body_example = _example_from_schema(operation.body_schema) if operation.body_schema else None
    return {
        "id": operation.id,
        "method": operation.method,
        "path": operation.path,
        "summary": operation.summary,
        "description": operation.description,
        "category": operation.category,
        "permission": operation.permission,
        "read_only": operation.read_only,
        "requires_approval": not operation.read_only,
        "supported": operation.supported,
        "parameters": operation.parameters,
        "body_schema": operation.body_schema,
        "body_required": operation.body_required,
        "body_example": body_example,
        "note": "" if operation.supported else "该接口需要文件或表单上传，请在对应页面操作；助手可以帮你打开页面。",
    }


def _example_from_schema(schema: Any, depth: int = 0) -> Any:
    if depth > 5 or not isinstance(schema, dict):
        return None
    if schema.get("default") is not None:
        return schema["default"]
    if schema.get("enum"):
        return schema["enum"][0]
    schema_type = schema.get("type")
    if schema_type == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return {}
        required = set(schema.get("required") or [])
        result: dict[str, Any] = {}
        for name, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            if name in required or len(result) < 6:
                example = _example_from_schema(prop, depth + 1)
                if example is not None:
                    result[name] = example
        return result
    if schema_type == "array":
        item = _example_from_schema(schema.get("items"), depth + 1)
        return [item] if item is not None else []
    if schema_type == "integer":
        return schema.get("minimum", 1)
    if schema_type == "number":
        return schema.get("minimum", 1)
    if schema_type == "boolean":
        return False
    if schema_type == "string":
        fmt = schema.get("format")
        if fmt == "date-time":
            return "2026-01-01T00:00:00Z"
        return "示例"
    return None


def catalog_payload(operation: ApiOperation) -> dict[str, Any]:
    required = [param["name"] for param in operation.parameters if param["required"]]
    return {
        "id": operation.id,
        "method": operation.method,
        "path": operation.path,
        "summary": operation.summary,
        "description": operation.description,
        "category": operation.category,
        "permission": operation.permission,
        "read_only": operation.read_only,
        "requires_approval": not operation.read_only,
        "supported": operation.supported,
        "required_params": required,
        "has_body": bool(operation.body_schema),
        "body_required": operation.body_required,
    }


def _page_for_path(path: str) -> dict[str, str] | None:
    for pattern, perm in PAGE_DETAIL_PERMS:
        if pattern.match(path):
            return {"path": path, "label": "日志详情", "perm": perm}
    best: dict[str, str] | None = None
    for item in PAGE_DIRECTORY:
        prefix = item["path"]
        if prefix == "/":
            if path == "/":
                return dict(item)
            continue
        if path == prefix or path.startswith(prefix + "/"):
            if best is None or len(prefix) > len(best["path"]):
                best = item
    return dict(best) if best is not None else None


def page_directory(permissions: set[str] | None = None) -> list[dict[str, str]]:
    if permissions is None:
        return [dict(item) for item in PAGE_DIRECTORY]
    return [dict(item) for item in PAGE_DIRECTORY if has_permission(permissions, item["perm"])]


def normalize_navigation_path(path: str, permissions: set[str] | None = None) -> str | None:
    value = (path or "").strip()
    if not value.startswith("/") or value.startswith("//") or len(value) > 300:
        return None
    base = value.split("?", 1)[0].split("#", 1)[0]
    decoded = unquote(unquote(base))
    if "\\" in decoded:
        return None
    segments = [segment for segment in decoded.split("/") if segment]
    if any(segment in {".", ".."} for segment in segments):
        return None
    if base in {"/login", "/share", "/voice/join"}:
        return None
    if base.rstrip("/") == "":
        base = "/"
    page = _page_for_path(base)
    if page is None:
        return None
    if permissions is not None and not has_permission(permissions, page["perm"]):
        return None
    return value


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)

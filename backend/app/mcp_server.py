from __future__ import annotations

import inspect
import json
from contextvars import ContextVar
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from app.capabilities import ensure_defaults, invoke_tool, list_tool_defs, resolve_tool
from app.capabilities.base import CapabilityError
from app.db import get_session_factory
from app.deps import extract_raw_api_key
from app.services.mcp_auth import allowed_capability_ids, resolve_mcp_key

_current_mcp_key_id: ContextVar[int | None] = ContextVar("current_mcp_key_id", default=None)

# streamable_http_path="/"：外层 app.mount("/mcp") 会把 /mcp 剥成 / 或空路径。
# FastMCP 默认挂 /mcp，叠在一起后只有 /mcp/ 能进 MCP，文档写的 /mcp 会被 SPA 吃掉。
mcp = FastMCP(
    "llm-gateway-capabilities",
    json_response=True,
    stateless_http=True,
    streamable_http_path="/",
)
_mcp_asgi_app = None
_tools_registered = False


def get_mcp() -> FastMCP:
    return mcp


class McpAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        authorization = request.headers.get("authorization")
        x_api_key = request.headers.get("x-api-key")
        raw = extract_raw_api_key(authorization, x_api_key)
        session = get_session_factory()()
        token = None
        try:
            key = resolve_mcp_key(session, raw)
            if key is None or key.status != "active":
                return JSONResponse(
                    status_code=401,
                    content={"error": {"type": "authentication_error", "message": "无效的 MCP Key"}},
                )
            token = _current_mcp_key_id.set(key.id)
            request.state.mcp_key_id = key.id
        finally:
            session.close()
        try:
            return await call_next(request)
        finally:
            if token is not None:
                _current_mcp_key_id.reset(token)


def _load_mcp_key(db, key_id: int | None):
    if key_id is None:
        return None
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models import McpKey

    return db.scalar(select(McpKey).where(McpKey.id == key_id).options(selectinload(McpKey.capabilities)))


async def _run_registered_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    ensure_defaults()
    key_id = _current_mcp_key_id.get()
    session = get_session_factory()()
    try:
        mcp_key = _load_mcp_key(session, key_id)
        if mcp_key is None or mcp_key.status != "active":
            return json.dumps(
                {"error": {"type": "authentication_error", "message": "无效的 MCP Key"}},
                ensure_ascii=False,
            )
        result = await invoke_tool(session, mcp_key=mcp_key, tool_name=tool_name, payload=arguments)
        session.commit()
        return json.dumps(result, ensure_ascii=False, default=str)
    except CapabilityError as error:
        session.rollback()
        return json.dumps({"error": {"type": error.error_type, "message": error.message}}, ensure_ascii=False)
    except Exception as error:
        session.rollback()
        return json.dumps({"error": {"type": "internal_error", "message": str(error)}}, ensure_ascii=False)
    finally:
        session.close()


_JSON_SCHEMA_TYPES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _annotation_for(schema: dict[str, Any]) -> Any:
    json_type = schema.get("type")
    if isinstance(json_type, str) and json_type in _JSON_SCHEMA_TYPES:
        return _JSON_SCHEMA_TYPES[json_type]
    return Any


def _build_signature(properties: dict[str, Any], required: list[str]) -> inspect.Signature:
    req = set(required or [])
    # 必填参数必须排在可选参数之前，否则 Python 语法不合法
    ordered = [name for name in (required or []) if name in properties]
    ordered += [name for name in properties if name not in req]
    parameters: list[inspect.Parameter] = []
    for name in ordered:
        annotation = _annotation_for(properties.get(name) or {})
        if name in req:
            parameters.append(
                inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=annotation)
            )
        else:
            parameters.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=annotation | None,
                    default=None,
                )
            )
    return inspect.Signature(parameters)


def _register_tool_from_def(
    tool_name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> None:
    """按声明的输入 schema 生成显式签名，FastMCP 据此产出可读的 tools/list。

    用 inspect.Signature 而不是 exec：参数顺序安全，且 schema 类型能映射到 Python 类型。
    """

    async def handler(**kwargs: Any) -> str:
        payload = {key: value for key, value in kwargs.items() if value is not None}
        return await _run_registered_tool(tool_name, payload)

    handler.__name__ = tool_name.replace("-", "_")
    handler.__doc__ = description
    handler.__signature__ = _build_signature(properties or {}, required or [])  # type: ignore[attr-defined]
    mcp.add_tool(handler, name=tool_name, description=description, structured_output=False)


def register_all_tools() -> None:
    global _tools_registered
    if _tools_registered:
        return
    ensure_defaults()
    for _capability_id, tool in list_tool_defs(only_enabled=True):
        schema = tool.input_schema if isinstance(tool.input_schema, dict) else {}
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        _register_tool_from_def(tool.name, tool.description, properties, required)

    # tools/list 按当前请求 MCP Key 白名单过滤
    _original_list_tools = mcp.list_tools

    async def list_tools_filtered():
        tools = await _original_list_tools()
        key_id = _current_mcp_key_id.get()
        if key_id is None:
            return []
        session = get_session_factory()()
        try:
            mcp_key = _load_mcp_key(session, key_id)
            if mcp_key is None:
                return []
            allowed = allowed_capability_ids(mcp_key)
        finally:
            session.close()
        filtered = []
        for tool in tools:
            resolved = resolve_tool(tool.name)
            if resolved is None:
                continue
            capability_id, _operation = resolved
            if capability_id in allowed:
                filtered.append(tool)
        return filtered

    mcp.list_tools = list_tools_filtered  # type: ignore[method-assign]
    _tools_registered = True


class McpStreamableEndpoint:
    """请求期动态解析 session manager，避免子应用绑定到已销毁的实例。

    用类而不是函数：Starlette 会把函数端点包装成只接受 GET 的 request-response，
    这里需要的是接收任意方法的裸 ASGI。
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            manager = mcp.session_manager
        except RuntimeError:
            response = JSONResponse(
                {"error": {"type": "server_error", "message": "MCP 服务未就绪"}},
                status_code=503,
            )
            await response(scope, receive, send)
            return
        await manager.handle_request(scope, receive, send)


class McpRootEntrypoint:
    """/mcp（无尾斜杠）入口。

    Starlette 的 Mount("/mcp") 只匹配 /mcp/...，无尾斜杠会被 SPA 兜底成 404。
    这里把路径改写成内部 "/" 后转交同一个 MCP 子应用，不依赖客户端跟随 307。
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope = dict(scope)
        scope["path"] = "/"
        scope["raw_path"] = b"/"
        scope["root_path"] = scope.get("root_path", "") + "/mcp"
        await self.app(scope, receive, send)


def build_mcp_app():
    global _mcp_asgi_app
    ensure_defaults()
    register_all_tools()
    if _mcp_asgi_app is None:
        app = Starlette(routes=[Route("/", endpoint=McpStreamableEndpoint())])
        app.add_middleware(McpAuthMiddleware)
        _mcp_asgi_app = app
    return _mcp_asgi_app


def start_mcp_session():
    """在父应用 lifespan 中启动 session manager。

    Starlette 只运行顶层 lifespan，mounted 子应用的 lifespan 不会执行；而 SDK 把
    ``session_manager.run()`` 放在子应用 lifespan 里。不接管就会出现
    "Task group is not initialized" 的 500（鉴权已过、handler 才炸）。这里返回一个
    已创建的 manager，由调用方 ``async with manager.run()`` 覆盖整个应用生命周期。

    ``run()`` 每个实例只能调用一次，所以每次启动都重建 manager，保证测试或进程内
    重启拿到干净实例；子应用通过 ``McpStreamableEndpoint`` 动态取当前 manager，无需重挂。
    """
    ensure_defaults()
    register_all_tools()
    mcp._session_manager = None
    mcp.streamable_http_app()
    return mcp.session_manager

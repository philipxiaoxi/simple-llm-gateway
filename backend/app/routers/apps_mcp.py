from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import extract_raw_api_key, resolve_api_key
from app.models import ApiKey
from app.schemas import AppOcrResultOut, AppOut, StaticSiteCreate, StaticSiteOut
from app.services.apps import static_deploy as static_service
from app.services.apps.mcp_tools import (
    call_tool,
    enabled_app_summary,
    mcp_server_info,
    openai_tools,
    recognize_upload,
    tool_definitions,
)
from app.services.apps.service import list_apps, require_enabled_app

router = APIRouter(tags=["apps-mcp"])


def require_api_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> ApiKey:
    raw = extract_raw_api_key(authorization, x_api_key)
    api_key = resolve_api_key(db, raw)
    if api_key is None or api_key.status != "active":
        raise HTTPException(status_code=401, detail="无效或未启用的 API Key")
    return api_key


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _jsonrpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": _json_safe(result)}


def _jsonrpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = _json_safe(data)
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


async def _handle_mcp_method(db: Session, method: str, params: dict[str, Any] | None, request_id: Any) -> dict[str, Any]:
    params = params if isinstance(params, dict) else {}
    if method in {"initialize", "mcp/initialize"}:
        return _jsonrpc_result(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": mcp_server_info(),
            },
        )
    if method in {"notifications/initialized", "initialized"}:
        return _jsonrpc_result(request_id, {})
    if method in {"ping", "mcp/ping"}:
        return _jsonrpc_result(request_id, {})
    if method in {"tools/list", "list_tools"}:
        tools = tool_definitions(db)
        return _jsonrpc_result(request_id, {"tools": tools})
    if method in {"tools/call", "call_tool"}:
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            result = await call_tool(db, name, arguments)
            return _jsonrpc_result(
                request_id,
                {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}],
                    "structuredContent": result,
                    "isError": False,
                },
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
            return _jsonrpc_result(
                request_id,
                {
                    "content": [{"type": "text", "text": detail}],
                    "isError": True,
                },
            )
    return _jsonrpc_error(request_id, -32601, f"Method not found: {method}")


@router.api_route("/mcp", methods=["GET", "POST", "DELETE"])
@router.api_route("/api/mcp", methods=["GET", "POST", "DELETE"])
async def mcp_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    _api_key: ApiKey = Depends(require_api_key),
):
    """Streamable HTTP 风格 MCP：JSON-RPC over POST；GET 返回服务说明。"""
    if request.method == "GET":
        return {
            "server": mcp_server_info(),
            "transport": "streamable-http",
            "endpoint": str(request.url.path),
            "auth": "Authorization: Bearer <API_KEY> 或 x-api-key",
            "methods": ["initialize", "tools/list", "tools/call", "ping"],
            "tools": tool_definitions(db),
            "openai_tools": openai_tools(db),
        }
    if request.method == "DELETE":
        return Response(status_code=204)

    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(400, "请求体必须是 JSON") from exc

    async def handle_one(message: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return _jsonrpc_error(None, -32600, "Invalid Request")
        # notification（无 id）可忽略
        if "id" not in message and str(message.get("method") or "").startswith("notifications/"):
            return None
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        request_id = message.get("id")
        if not method:
            return _jsonrpc_error(request_id, -32600, "Missing method")
        return await _handle_mcp_method(db, method, params, request_id)

    if isinstance(body, list):
        replies = []
        for item in body:
            reply = await handle_one(item)
            if reply is not None:
                replies.append(reply)
        return JSONResponse(replies)
    if isinstance(body, dict):
        reply = await handle_one(body)
        if reply is None:
            return Response(status_code=202)
        return JSONResponse(reply)
    raise HTTPException(400, "无效的 JSON-RPC 请求")


@router.get("/api/apps")
def public_list_apps(
    db: Session = Depends(get_db),
    _api_key: ApiKey = Depends(require_api_key),
):
    return {"items": enabled_app_summary(db), "total": len(enabled_app_summary(db))}


@router.get("/api/apps/tools")
def public_list_tools(
    db: Session = Depends(get_db),
    _api_key: ApiKey = Depends(require_api_key),
):
    return {
        "server": mcp_server_info(),
        "tools": tool_definitions(db),
        "openai_tools": openai_tools(db),
    }


@router.post("/api/apps/tools/call")
async def public_call_tool(
    request: Request,
    db: Session = Depends(get_db),
    _api_key: ApiKey = Depends(require_api_key),
):
    body = await request.json()
    name = str((body or {}).get("name") or "")
    arguments = (body or {}).get("arguments") if isinstance((body or {}).get("arguments"), dict) else {}
    result = await call_tool(db, name, arguments)
    return {"ok": True, "name": name, "result": result}


@router.post("/api/apps/ocr/recognize", response_model=AppOcrResultOut)
async def public_ocr_recognize(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _api_key: ApiKey = Depends(require_api_key),
):
    data = await recognize_upload(db, file)
    return AppOcrResultOut(**data)


@router.get("/api/apps/static-deploy/sites", response_model=list[StaticSiteOut])
def public_list_sites(
    db: Session = Depends(get_db),
    _api_key: ApiKey = Depends(require_api_key),
):
    require_enabled_app(db, "static-deploy")
    return [StaticSiteOut(**item) for item in static_service.list_sites(db)]


@router.post("/api/apps/static-deploy/sites", response_model=StaticSiteOut, status_code=201)
def public_create_site(
    payload: StaticSiteCreate,
    db: Session = Depends(get_db),
    _api_key: ApiKey = Depends(require_api_key),
):
    result = static_service.create_site(
        db,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
    )
    db.commit()
    return StaticSiteOut(**result)


@router.delete("/api/apps/static-deploy/sites/{site_id}", status_code=204)
def public_delete_site(
    site_id: int,
    db: Session = Depends(get_db),
    _api_key: ApiKey = Depends(require_api_key),
):
    static_service.delete_site(db, site_id)
    db.commit()


@router.post("/api/apps/static-deploy/sites/{site_id}/upload", response_model=StaticSiteOut)
async def public_upload_site(
    site_id: int,
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    db: Session = Depends(get_db),
    _api_key: ApiKey = Depends(require_api_key),
):
    if file is not None and file.filename:
        payload = await file.read()
        name = (file.filename or "").lower()
        if name.endswith(".zip") or (file.content_type or "").endswith("zip"):
            result = static_service.deploy_zip(db, site_id, payload, file.filename)
        else:
            result = static_service.deploy_files(db, site_id, [(file.filename or "index.html", payload)])
        db.commit()
        return StaticSiteOut(**result)
    upload_list = files or []
    if not upload_list:
        raise HTTPException(400, "请上传 zip 或静态文件")
    pairs: list[tuple[str, bytes]] = []
    for index, item in enumerate(upload_list):
        content = await item.read()
        pairs.append((item.filename or f"file-{index}", content))
    result = static_service.deploy_files(db, site_id, pairs)
    db.commit()
    return StaticSiteOut(**result)


@router.get("/api/apps/integration")
def integration_guide(
    request: Request,
    db: Session = Depends(get_db),
    _api_key: ApiKey = Depends(require_api_key),
):
    base = str(request.base_url).rstrip("/")
    return {
        "mcp": {
            "url": f"{base}/mcp",
            "alt_url": f"{base}/api/mcp",
            "headers": {"Authorization": "Bearer <YOUR_API_KEY>"},
            "cursor_example": {
                "mcpServers": {
                    "gateway-app-center": {
                        "url": f"{base}/mcp",
                        "headers": {"Authorization": "Bearer <YOUR_API_KEY>"},
                    }
                }
            },
        },
        "rest": {
            "tools": f"{base}/api/apps/tools",
            "call": f"{base}/api/apps/tools/call",
            "ocr": f"{base}/api/apps/ocr/recognize",
            "static_sites": f"{base}/api/apps/static-deploy/sites",
        },
        "apps": enabled_app_summary(db),
        "tools": tool_definitions(db),
    }

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.capabilities import ensure_defaults, get_provider, invoke_capability, list_specs
from app.capabilities.base import CapabilityError
from app.db import get_db
from app.schemas import KnowledgeSearchRequest, KnowledgeSearchResponse, McpCapabilityOut
from app.services.mcp_auth import allowed_capability_ids, get_mcp_key_from_headers

router = APIRouter(tags=["capabilities"])


def _error(status: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"type": error_type, "message": message}},
    )


def _mcp_key_dep(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    return get_mcp_key_from_headers(db, authorization, x_api_key)


def _spec_out(spec) -> McpCapabilityOut:
    return McpCapabilityOut(
        capability_id=spec.capability_id,
        name=spec.name,
        description=spec.description,
        version=spec.version,
        category=spec.category,
        status=spec.status,
        input_schema=spec.input_schema,
        admin_path=getattr(spec, "admin_path", "") or "",
        icon=getattr(spec, "icon", "") or "",
    )


@router.get("/v1/capabilities", response_model=list[McpCapabilityOut])
def list_capabilities(mcp_key=Depends(_mcp_key_dep)):
    ensure_defaults()
    allowed = allowed_capability_ids(mcp_key)
    return [_spec_out(spec) for spec in list_specs(only_enabled=True) if spec.capability_id in allowed]


@router.post("/v1/capabilities/{capability_id}/{operation}")
async def dispatch_capability(
    capability_id: str,
    operation: str,
    request: Request,
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    """通用能力调用入口。新应用只需实现 Provider.dispatch，无需再加专用 REST 路由。"""
    ensure_defaults()
    payload: dict[str, Any] = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
            if isinstance(body, dict):
                payload = body
        except Exception:
            payload = {}
    try:
        return await invoke_capability(
            db,
            mcp_key=mcp_key,
            capability_id=capability_id,
            operation=operation,
            payload=payload,
        )
    except CapabilityError as error:
        return _error(error.status_code, error.error_type, error.message)


@router.get("/v1/capabilities/knowledge/bases")
async def knowledge_bases_compat(db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    """兼容旧路径 → knowledge/list。"""
    ensure_defaults()
    try:
        return await invoke_capability(db, mcp_key=mcp_key, capability_id="knowledge", operation="list", payload={})
    except CapabilityError as error:
        return _error(error.status_code, error.error_type, error.message)


@router.post("/v1/capabilities/knowledge/search", response_model=KnowledgeSearchResponse)
async def knowledge_search_compat(
    payload: KnowledgeSearchRequest,
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    """兼容旧路径 → knowledge/search。"""
    ensure_defaults()
    try:
        result = await invoke_capability(
            db,
            mcp_key=mcp_key,
            capability_id="knowledge",
            operation="search",
            payload=payload.model_dump(),
        )
        return KnowledgeSearchResponse(**result)
    except CapabilityError as error:
        return _error(error.status_code, error.error_type, error.message)


@router.get("/v1/capabilities/{capability_id}", response_model=McpCapabilityOut)
def get_capability(capability_id: str, mcp_key=Depends(_mcp_key_dep)):
    ensure_defaults()
    provider = get_provider(capability_id)
    if provider is None or provider.spec.status != "enabled":
        raise HTTPException(
            status_code=404,
            detail={"error": {"type": "not_found", "message": "能力不存在或已停用"}},
        )
    if capability_id not in allowed_capability_ids(mcp_key):
        raise HTTPException(
            status_code=403,
            detail={"error": {"type": "permission_error", "message": f"MCP Key 未授权能力 {capability_id}"}},
        )
    return _spec_out(provider.spec)

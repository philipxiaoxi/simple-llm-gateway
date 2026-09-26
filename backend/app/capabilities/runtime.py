from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from app.capabilities.base import CallContext, CapabilityError, Provider
from app.capabilities.registry import get_provider, resolve_tool
from app.clock import utcnow
from app.models import McpKey
from app.capabilities.docparse.errors import DocParseError
from app.capabilities.site.errors import SiteError
from app.services.knowledge import KnowledgeError
from app.services.mcp_auth import allowed_capability_ids
from app.services.mcp_logs import record_mcp_call


def _normalize_error(error: Exception) -> CapabilityError:
    if isinstance(error, CapabilityError):
        return error
    if isinstance(error, (KnowledgeError, DocParseError, SiteError)):
        return CapabilityError(error.message, status_code=error.status_code, error_type=error.error_type)
    return CapabilityError(str(error), status_code=500, error_type="internal_error")


async def invoke_capability(
    db: Session,
    *,
    mcp_key: McpKey,
    capability_id: str,
    operation: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_provider = get_provider(capability_id)
    if ensure_provider is None or ensure_provider.spec.status != "enabled":
        raise CapabilityError("能力不存在或已停用", status_code=404, error_type="not_found")
    if capability_id not in allowed_capability_ids(mcp_key):
        raise CapabilityError(f"MCP Key 未授权能力 {capability_id}", status_code=403, error_type="permission_error")

    started = time.perf_counter()
    body = payload or {}
    try:
        result = await ensure_provider.dispatch(operation, body, CallContext(db=db, mcp_key=mcp_key))
        record_mcp_call(
            db,
            mcp_key=mcp_key,
            capability_id=capability_id,
            operation=operation,
            success=True,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        mcp_key.last_used_at = utcnow()
        return result if isinstance(result, dict) else {"result": result}
    except Exception as error:
        cap_error = _normalize_error(error)
        record_mcp_call(
            db,
            mcp_key=mcp_key,
            capability_id=capability_id,
            operation=operation,
            success=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error_message=cap_error.message,
        )
        raise cap_error from error


async def invoke_tool(
    db: Session,
    *,
    mcp_key: McpKey,
    tool_name: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = resolve_tool(tool_name)
    if resolved is None:
        raise CapabilityError(f"未知工具: {tool_name}", status_code=404, error_type="not_found")
    capability_id, operation = resolved
    return await invoke_capability(
        db,
        mcp_key=mcp_key,
        capability_id=capability_id,
        operation=operation,
        payload=payload,
    )


def require_provider(capability_id: str) -> Provider:
    provider = get_provider(capability_id)
    if provider is None or provider.spec.status != "enabled":
        raise CapabilityError("能力不存在或已停用", status_code=404, error_type="not_found")
    return provider

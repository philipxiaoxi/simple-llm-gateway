from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import McpCallLog, McpKey


def record_mcp_call(
    db: Session,
    *,
    mcp_key: McpKey | None,
    capability_id: str,
    operation: str,
    success: bool,
    latency_ms: int,
    error_message: str | None = None,
    request_meta_json: str | None = None,
) -> None:
    db.add(
        McpCallLog(
            mcp_key_id=mcp_key.id if mcp_key else None,
            mcp_key_name=mcp_key.name if mcp_key else None,
            mcp_key_prefix=mcp_key.key_prefix if mcp_key else None,
            capability_id=capability_id,
            operation=operation,
            success=success,
            latency_ms=max(0, int(latency_ms)),
            error_message=(error_message or "")[:500] or None,
            request_meta_json=request_meta_json,
        )
    )

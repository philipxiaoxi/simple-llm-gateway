from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.models import McpKey


@dataclass
class CallContext:
    db: Session
    mcp_key: McpKey | None = None
    request_id: str = ""


@dataclass
class McpToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]
    operation: str = ""

    def resolved_operation(self) -> str:
        return self.operation or self.name


@dataclass
class CapabilitySpec:
    capability_id: str
    name: str
    description: str
    version: str = "1.0.0"
    category: str = ""
    status: str = "enabled"
    input_schema: dict[str, Any] = field(default_factory=dict)
    # 管理端应用入口（相对路径）。空表示无独立管理页，仅协议面能力。
    admin_path: str = ""
    # 广场卡片图标名（前端映射），可选
    icon: str = ""


class Provider(Protocol):
    spec: CapabilitySpec

    def list_mcp_tools(self) -> list[McpToolDef]: ...

    async def dispatch(self, operation: str, payload: dict[str, Any], ctx: CallContext) -> dict[str, Any]: ...


class CapabilityError(Exception):
    def __init__(self, message: str, status_code: int = 400, error_type: str = "invalid_request") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type

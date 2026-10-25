from __future__ import annotations

import json
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, TypeVar

from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models import McpCallLog, McpKey
from app.services.headers_redact import dump_headers

T = TypeVar("T")

_BODY_LIMIT = 20000


@dataclass
class CallMeta:
    """一次调用的来源信息（进程内上下文，不落库时无需关心）。"""

    transport: str = "rest"
    client_ip: str | None = None
    user_agent: str | None = None
    client_name: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


_meta_var: ContextVar[CallMeta | None] = ContextVar("mcp_call_meta", default=None)


def set_call_meta(meta: CallMeta) -> Token[CallMeta | None]:
    return _meta_var.set(meta)


def reset_call_meta(token: Token[CallMeta | None]) -> None:
    _meta_var.reset(token)


def current_meta() -> CallMeta | None:
    return _meta_var.get()


def _dump_body(value: Any) -> str | None:
    if value is None:
        return None
    try:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    text = text.strip()
    if not text:
        return None
    return text[:_BODY_LIMIT]


def record_mcp_call(
    db: Session,
    *,
    mcp_key: McpKey | None,
    capability_id: str,
    operation: str,
    success: bool,
    latency_ms: int,
    error_message: str | None = None,
    error_type: str | None = None,
    http_status: int | None = None,
    request_body: Any = None,
    response_body: Any = None,
    request_meta_json: str | None = None,
    meta: CallMeta | None = None,
) -> None:
    source = meta if meta is not None else current_meta()
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
            error_type=(error_type or "")[:64] or None,
            http_status=http_status,
            request_body=_dump_body(request_body),
            response_body=_dump_body(response_body),
            transport=(source.transport if source else None),
            request_headers=dump_headers(source.headers) if source else None,
            client_ip=(source.client_ip if source else None),
            user_agent=(source.user_agent if source else None),
            client_name=(source.client_name if source else None),
            request_meta_json=request_meta_json,
        )
    )
    if mcp_key is not None and success:
        mcp_key.last_used_at = utcnow()


class McpCallRecorder:
    """专用 REST 入口的调用日志上下文。成功/失败都会落库。"""

    def __init__(
        self, db: Session, mcp_key: McpKey, capability_id: str, operation: str, meta: CallMeta | None = None
    ) -> None:
        self.db = db
        self.mcp_key = mcp_key
        self.capability_id = capability_id
        self.operation = operation
        self.meta = meta if meta is not None else current_meta()
        self.request_body: Any = None
        self.response_body: Any = None
        self._started = time.perf_counter()
        self._closed = False

    def set_request(self, body: Any) -> None:
        self.request_body = body

    def set_response(self, body: Any) -> None:
        self.response_body = body

    def _latency_ms(self) -> int:
        return int((time.perf_counter() - self._started) * 1000)

    def success(self, *, http_status: int = 200) -> None:
        if self._closed:
            return
        record_mcp_call(
            self.db,
            mcp_key=self.mcp_key,
            capability_id=self.capability_id,
            operation=self.operation,
            success=True,
            latency_ms=self._latency_ms(),
            http_status=http_status,
            request_body=self.request_body,
            response_body=self.response_body,
            meta=self.meta,
        )
        self._closed = True

    def failure(
        self, message: str, *, error_type: str | None = None, http_status: int | None = None
    ) -> None:
        if self._closed:
            return
        record_mcp_call(
            self.db,
            mcp_key=self.mcp_key,
            capability_id=self.capability_id,
            operation=self.operation,
            success=False,
            latency_ms=self._latency_ms(),
            error_message=message,
            error_type=error_type,
            http_status=http_status,
            request_body=self.request_body,
            response_body=self.response_body,
            meta=self.meta,
        )
        self._closed = True

    def finish(self, *, ok: bool, message: str | None = None) -> None:
        if ok:
            self.success()
            return
        self.failure(message or "请求失败")


def begin_mcp_call(
    db: Session,
    mcp_key: McpKey,
    capability_id: str,
    operation: str,
    meta: CallMeta | None = None,
) -> McpCallRecorder:
    return McpCallRecorder(db, mcp_key, capability_id, operation, meta)

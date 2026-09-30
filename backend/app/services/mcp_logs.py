from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models import McpCallLog, McpKey

T = TypeVar("T")


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
    if mcp_key is not None and success:
        mcp_key.last_used_at = utcnow()


class McpCallRecorder:
    """专用 REST 入口的调用日志上下文。成功/失败都会落库。"""

    def __init__(self, db: Session, mcp_key: McpKey, capability_id: str, operation: str) -> None:
        self.db = db
        self.mcp_key = mcp_key
        self.capability_id = capability_id
        self.operation = operation
        self._started = time.perf_counter()
        self._closed = False

    def _latency_ms(self) -> int:
        return int((time.perf_counter() - self._started) * 1000)

    def success(self) -> None:
        if self._closed:
            return
        record_mcp_call(
            self.db,
            mcp_key=self.mcp_key,
            capability_id=self.capability_id,
            operation=self.operation,
            success=True,
            latency_ms=self._latency_ms(),
        )
        self._closed = True

    def failure(self, message: str) -> None:
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
        )
        self._closed = True

    def finish(self, *, ok: bool, message: str | None = None) -> None:
        if ok:
            self.success()
            return
        self.failure(message or "请求失败")


def begin_mcp_call(db: Session, mcp_key: McpKey, capability_id: str, operation: str) -> McpCallRecorder:
    return McpCallRecorder(db, mcp_key, capability_id, operation)

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """返回 naive UTC 当前时间（替代已弃用的 datetime.utcnow）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)

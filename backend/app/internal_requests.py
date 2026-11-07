"""进程内受信请求标记。

站内 AI 助手等内部服务通过 ASGI 直接调用管理接口时，会带上一个仅存在于当前进程
内存的随机令牌。安全审计中间件据此跳过判定，避免把自家人调用误报成扫描器请求。

令牌不落库、不写日志、不暴露给前端，外部客户端无法伪造。
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Mapping

INTERNAL_AUDIT_HEADER = "x-internal-audit-token"

_INTERNAL_AUDIT_TOKEN = secrets.token_urlsafe(32)


def internal_audit_headers() -> dict[str, str]:
    """返回内部调用需要携带的审计豁免头。"""
    return {INTERNAL_AUDIT_HEADER: _INTERNAL_AUDIT_TOKEN}


def is_internal_audit_request(headers: Mapping[str, str] | None) -> bool:
    """判断请求是否来自当前进程内的受信调用。"""
    if not headers:
        return False
    value = None
    for key, item in headers.items():
        if str(key).lower() == INTERNAL_AUDIT_HEADER:
            value = str(item)
            break
    if not value:
        return False
    try:
        return hmac.compare_digest(value, _INTERNAL_AUDIT_TOKEN)
    except TypeError:
        return False

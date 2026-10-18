"""请求头脱敏。

网关收到的请求头里可能带 API Key、OAuth Token、Cookie 等凭据，
入库前必须打码，只保留可用于排查的非敏感头。
"""

from __future__ import annotations

import json

_REDACTED = "<redacted>"
_VALUE_LIMIT = 2048
_TOTAL_LIMIT = 16384

# 头名包含任一子串即视为敏感，整条值打码
_SENSITIVE_SUBSTRINGS = (
    "authorization",
    "auth",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "api-key",
    "api_key",
    "apikey",
    "cookie",
    "jwt",
    "bearer",
    "signature",
    "private-key",
    "key",
)


def is_sensitive_header(name: str) -> bool:
    lowered = str(name).lower()
    return any(needle in lowered for needle in _SENSITIVE_SUBSTRINGS)


def redact_headers(headers: dict[str, str] | None) -> dict[str, str] | None:
    if not headers:
        return None
    redacted: dict[str, str] = {}
    for name, value in headers.items():
        text = str(name)
        if is_sensitive_header(text):
            redacted[text] = _REDACTED
        else:
            redacted[text] = str(value)[:_VALUE_LIMIT]
    return redacted


def dump_headers(headers: dict[str, str] | None) -> str | None:
    redacted = redact_headers(headers)
    if redacted is None:
        return None
    # 超大时丢弃最长的几项，保证落库的是合法 JSON，而不是被截断的半截
    while redacted and len(json.dumps(redacted, ensure_ascii=False).encode("utf-8")) > _TOTAL_LIMIT:
        longest = max(redacted, key=lambda key: len(redacted[key]))
        redacted.pop(longest)
    if not redacted:
        return None
    return json.dumps(redacted, ensure_ascii=False)

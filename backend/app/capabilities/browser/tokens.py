"""截图访问令牌：自包含截图 id + 过期时间的 HMAC 签名令牌。

格式：base64url("shot:<id>").expires.signature
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from app.config import get_settings

_SEPARATOR = "."
_SIGNATURE_BYTES = 16
_SCOPE = "shot"


def _secret() -> bytes:
    return get_settings().app_secret_key.encode("utf-8")


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload: str) -> str:
    digest = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    return digest[:_SIGNATURE_BYTES].hex()


def make_token(screenshot_id: str, *, ttl_seconds: int | None = None) -> str:
    ttl = get_settings().browser_screenshot_ttl_seconds if ttl_seconds is None else ttl_seconds
    expires = int(time.time()) + max(1, int(ttl))
    payload = f"{_b64e(f'{_SCOPE}:{screenshot_id}'.encode())}{_SEPARATOR}{expires}"
    return f"{payload}{_SEPARATOR}{_sign(payload)}"


def screenshot_id_from_token(token: str | None) -> str | None:
    if not token:
        return None
    parts = token.split(_SEPARATOR)
    if len(parts) != 3:
        return None
    encoded, raw_expires, signature = parts
    payload = f"{encoded}{_SEPARATOR}{raw_expires}"
    if not hmac.compare_digest(_sign(payload), signature):
        return None
    try:
        expires = int(raw_expires)
        decoded = _b64d(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if expires < int(time.time()):
        return None
    scope, _, screenshot_id = decoded.partition(":")
    if scope != _SCOPE or not screenshot_id:
        return None
    return screenshot_id

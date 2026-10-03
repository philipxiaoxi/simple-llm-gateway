from __future__ import annotations

import base64
import hashlib
import hmac
import time

from app.config import get_settings

TOKEN_SEPARATOR = "."
_SIGNATURE_BYTES = 16


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


def make_token(media_id: str, *, ttl_seconds: int | None = None) -> str:
    """生成自包含 media_id 的签名令牌：base64url(media_id).exp.signature。"""
    ttl = get_settings().douyin_download_token_ttl_seconds if ttl_seconds is None else ttl_seconds
    expires = int(time.time()) + max(1, int(ttl))
    payload = f"{_b64e(media_id.encode('utf-8'))}{TOKEN_SEPARATOR}{expires}"
    return f"{payload}{TOKEN_SEPARATOR}{_sign(payload)}"


def _verify(token: str) -> str | None:
    """校验签名与过期，返回其中的 media_id；无效或过期返回 None。"""
    if not token:
        return None
    parts = token.split(TOKEN_SEPARATOR)
    if len(parts) != 3:
        return None
    media_b64, raw_expires, signature = parts
    payload = f"{media_b64}{TOKEN_SEPARATOR}{raw_expires}"
    if not hmac.compare_digest(_sign(payload), signature):
        return None
    try:
        expires = int(raw_expires)
        media_id = _b64d(media_b64).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if not media_id or expires < int(time.time()):
        return None
    return media_id


def verify_token(media_id: str, token: str) -> bool:
    return _verify(token) == media_id


def media_id_from_token(token: str) -> str | None:
    """从令牌解析出 media_id（用于仅含 token 的公开分享链接）。"""
    return _verify(token)


def download_path(media_id: str, token: str | None = None) -> str:
    """同源相对下载路径。浏览器经前端/反代访问时按当前 origin 解析即可。"""
    path = f"/v1/douyin/media/{media_id}"
    if token:
        return f"{path}?token={token}"
    return path


def download_url(media_id: str, token: str | None = None) -> str:
    """带网关根地址的绝对下载地址，供服务端/脚本直接使用。"""
    base = get_settings().app_base_url.rstrip("/")
    return f"{base}{download_path(media_id, token)}"

from __future__ import annotations

import hashlib
import hmac
import time

from app.config import get_settings

TOKEN_SEPARATOR = "."


def _secret() -> bytes:
    return get_settings().app_secret_key.encode("utf-8")


def _sign(media_id: str, expires: int) -> str:
    message = f"{media_id}{TOKEN_SEPARATOR}{expires}".encode()
    return hmac.new(_secret(), message, hashlib.sha256).hexdigest()


def make_token(media_id: str, *, ttl_seconds: int | None = None) -> str:
    ttl = get_settings().douyin_download_token_ttl_seconds if ttl_seconds is None else ttl_seconds
    expires = int(time.time()) + max(1, int(ttl))
    return f"{expires}{TOKEN_SEPARATOR}{_sign(media_id, expires)}"


def verify_token(media_id: str, token: str) -> bool:
    if not token or TOKEN_SEPARATOR not in token:
        return False
    raw_expires, _, signature = token.partition(TOKEN_SEPARATOR)
    try:
        expires = int(raw_expires)
    except ValueError:
        return False
    if expires < int(time.time()):
        return False
    return hmac.compare_digest(_sign(media_id, expires), signature)


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

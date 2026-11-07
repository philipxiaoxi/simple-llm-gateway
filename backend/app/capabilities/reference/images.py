"""上游参考图代理与文件缓存。

图片由后端拉取后写入数据目录。缓存按 URL 哈希命名，重启后继续命中。
"""

from __future__ import annotations

import hashlib
import mimetypes
import threading
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.capabilities.reference.errors import ReferenceError
from app.config import get_settings

_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def proxy_path(route: str, relative_path: str) -> str:
    safe = safe_image_path(relative_path)
    path = f"/api/admin/mcp/{route}/images/{quote(safe, safe='/')}"
    return f"{path}?token={image_token(path)}"


def safe_image_path(relative_path: str) -> str:
    decoded = unquote(relative_path)
    return "/".join(part for part in decoded.split("/") if part not in {"", ".", ".."})


def image_token(path: str) -> str:
    from app.info.tokens import make_token

    return make_token(path, ttl_seconds=get_settings().references_image_token_ttl_seconds)


def image_token_valid(path: str, token: str) -> bool:
    from app.info.tokens import verify_token

    return verify_token(path, token)


def upstream_url(relative_path: str, base_url: str | None = None) -> str:
    settings = get_settings()
    base = (base_url or settings.references_image_cdn or settings.references_source_base).rstrip("/")
    relative = quote(safe_image_path(relative_path), safe="/")
    return f"{base}/{relative}"


def cached_image(kind: str, relative_path: str, base_url: str | None = None) -> tuple[Path, str]:
    url = upstream_url(relative_path, base_url)
    _validate_upstream(url, base_url)
    path = _cache_path(url)
    if path.is_file() and path.stat().st_size > 0:
        return path, _media_type(path)
    lock = _lock_for(path.name)
    with lock:
        if path.is_file() and path.stat().st_size > 0:
            return path, _media_type(path)
        data, media_type = _download(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(data)
        temporary.replace(path)
        return path, media_type


def _download(url: str) -> tuple[bytes, str]:
    settings = get_settings()
    timeout = max(5, settings.references_timeout_seconds)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise ReferenceError("参考图拉取失败", status_code=502, error_type="upstream_error") from error
    if len(response.content) > _MAX_IMAGE_BYTES:
        raise ReferenceError("参考图过大", status_code=502, error_type="upstream_error")
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not media_type.startswith("image/"):
        detected = _detect_media_type(response.content)
        if detected is None:
            raise ReferenceError("上游返回的不是图片", status_code=502, error_type="upstream_error")
        media_type = detected
    return response.content, media_type


def _validate_upstream(url: str, base_url: str | None = None) -> None:
    settings = get_settings()
    allowed = {
        urlsplit(settings.references_source_base).netloc.lower(),
        urlsplit(settings.references_image_cdn).netloc.lower(),
    }
    if base_url:
        allowed.add(urlsplit(base_url).netloc.lower())
    allowed.discard("")
    host = urlsplit(url).netloc.lower()
    if host not in allowed:
        raise ReferenceError("参考图地址不在允许范围内", status_code=400, error_type="invalid_request")


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        suffix = ".img"
    database = Path(get_settings().database_path).resolve()
    return database.parent / "cache" / "reference-images" / digest[:2] / f"{digest}{suffix}"


def _media_type(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0]
    return guessed or "application/octet-stream"


def _detect_media_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _lock_for(name: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(name)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[name] = lock
        return lock

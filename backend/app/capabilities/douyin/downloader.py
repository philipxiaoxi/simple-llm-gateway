from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin

import httpx

from app.capabilities.douyin.errors import DouyinError
from app.capabilities.douyin.resolver import assert_safe_url
from app.config import get_settings

MEDIA_PREFIXES = ("video/", "image/", "audio/")
EXT_BY_TYPE = {
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
}
DEFAULT_TYPE = {"video": "video/mp4", "image": "image/jpeg", "audio": "audio/mpeg"}
DEFAULT_EXT = {"video": "mp4", "image": "jpg", "audio": "mp3"}


def _allowed(content_type: str) -> bool:
    return any(content_type.startswith(prefix) for prefix in MEDIA_PREFIXES)


def extension_for(content_type: str, kind: str) -> str:
    if content_type in EXT_BY_TYPE:
        return EXT_BY_TYPE[content_type]
    subtype = content_type.split("/")[-1].split("+")[0].split(";")[0].strip()
    if subtype in {"mp4", "mpeg", "jpg", "jpeg", "png", "webp", "gif", "mov", "webm", "mp3", "m4a", "aac"}:
        return "jpg" if subtype == "jpeg" else subtype
    return DEFAULT_EXT.get(kind, "bin")


def final_filename(index: int, content_type: str, kind: str) -> str:
    return f"{index:03d}.{extension_for(content_type, kind)}"


def _open_stream(client: httpx.Client, url: str, max_redirects: int):
    current = url
    for _ in range(max(1, max_redirects) + 1):
        assert_safe_url(current)
        request = client.build_request("GET", current)
        response = client.send(request, stream=True)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location")
            response.close()
            if not location:
                raise DouyinError("重定向缺少 Location", status_code=400, error_type="unsafe_redirect")
            current = urljoin(current, location)
            continue
        return response
    raise DouyinError("重定向过多", status_code=400, error_type="unsafe_redirect")


def fetch_to_file(
    url: str,
    dest: Path,
    *,
    max_bytes: int,
    kind: str,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> tuple[int, str, str]:
    """流式下载到 dest，返回 (字节数, content_type, sha256)。

    on_progress(written, content_length) 在下载过程中被调用，用于上报进度；
    content_length 未知时为 None。
    """
    settings = get_settings()
    headers = {
        "User-Agent": settings.douyin_user_agent,
        "Referer": "https://www.douyin.com/",
    }
    tmp = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    total = 0
    with httpx.Client(
        timeout=settings.douyin_http_timeout_seconds, headers=headers, follow_redirects=False
    ) as client:
        response = _open_stream(client, url, settings.douyin_max_redirects)
        try:
            if response.status_code >= 400:
                raise DouyinError(
                    f"下载失败 HTTP {response.status_code}", status_code=422, error_type="download_failed"
                )
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if not content_type:
                content_type = DEFAULT_TYPE.get(kind, "application/octet-stream")
            if not _allowed(content_type):
                raise DouyinError(
                    f"不支持的媒体类型: {content_type}",
                    status_code=422,
                    error_type="unsupported_content_type",
                )
            length = response.headers.get("content-length")
            length_int = int(length) if length and length.isdigit() else None
            if length_int is not None and length_int > max_bytes:
                raise DouyinError("媒体项超过大小上限", status_code=413, error_type="too_large")
            if on_progress:
                on_progress(0, length_int)
            with tmp.open("wb") as handle:
                for chunk in response.iter_bytes(65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise DouyinError("媒体项超过大小上限", status_code=413, error_type="too_large")
                    handle.write(chunk)
                    digest.update(chunk)
                    if on_progress:
                        on_progress(total, length_int)
            if total == 0:
                raise DouyinError("下载内容为空", status_code=422, error_type="download_failed")
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            response.close()
    tmp.replace(dest)
    return total, content_type, digest.hexdigest()

"""媒体转存：把上游媒体落盘到本地数据卷。

Telegram 的媒体直链（`cdn4.telesco.pe`）带签名且会过期，官方也提示要尽快下载/转存，
因此采集时一律落到 `{INFO_MEDIA_PATH}/{item_id}/{index}.{ext}`，之后只对外提供本地地址。

上游不返回图片宽高，这里在落盘后用 Pillow 探测真实尺寸写库，供前端瀑布流精确占位。
"""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from app.config import get_settings
from app.info.errors import InfoError
from app.info.storage import media_path
from app.info.urlguard import MEDIA_HOSTS, assert_safe_url

ALLOWED_PREFIXES = ("image/", "video/")
EXT_BY_TYPE = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
}
DEFAULT_TYPE = {"image": "image/jpeg", "poster": "image/jpeg", "video": "video/mp4"}
DEFAULT_EXT = {"image": "jpg", "poster": "jpg", "video": "mp4"}
_GENERIC_TYPES = {"application/octet-stream", "binary/octet-stream", ""}


@dataclass(frozen=True)
class FetchedFile:
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    width: int | None
    height: int | None


def _allowed(content_type: str) -> bool:
    return any(content_type.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def extension_for(content_type: str, kind: str) -> str:
    if content_type in EXT_BY_TYPE:
        return EXT_BY_TYPE[content_type]
    subtype = content_type.split("/")[-1].split("+")[0].split(";")[0].strip()
    if subtype in {"mp4", "mpeg", "jpg", "jpeg", "png", "webp", "gif", "mov", "webm"}:
        return "jpg" if subtype == "jpeg" else subtype
    return DEFAULT_EXT.get(kind, "bin")


def final_filename(index: int, content_type: str, kind: str) -> str:
    return f"{index:03d}.{extension_for(content_type, kind)}"


def _resolve_content_type(header_value: str, url: str, kind: str) -> str:
    """telesco.pe 有时不返回具体类型，此时按 URL 后缀兜底推断。"""
    ctype = (header_value or "").split(";")[0].strip().lower()
    if ctype not in _GENERIC_TYPES:
        return ctype
    guessed = mimetypes.guess_type(urlparse(url).path)[0]
    if guessed:
        return guessed.lower()
    return DEFAULT_TYPE.get(kind, "application/octet-stream")


def _open_stream(client: httpx.Client, url: str, max_redirects: int) -> httpx.Response:
    current = url
    for _ in range(max(1, max_redirects) + 1):
        assert_safe_url(current, allow_hosts=MEDIA_HOSTS)
        request = client.build_request("GET", current)
        response = client.send(request, stream=True)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location")
            response.close()
            if not location:
                raise InfoError("重定向缺少 Location", status_code=400, error_type="unsafe_redirect")
            current = urljoin(current, location)
            continue
        return response
    raise InfoError("重定向过多", status_code=400, error_type="unsafe_redirect")


def probe_image_size(path: Path) -> tuple[int | None, int | None]:
    """用 Pillow 探测图片尺寸；视频文件或不支持的格式返回 (None, None)。"""
    try:
        from PIL import Image
    except Exception:  # Pillow 缺失时不影响采集，前端会回退到固定比例
        return None, None
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None, None


def fetch_media(
    item_id: str,
    *,
    index_no: int,
    remote_url: str,
    kind: str,
    max_bytes: int,
) -> FetchedFile:
    """流式下载单个媒体项到条目目录，返回落盘信息。失败不留下半成品。"""
    settings = get_settings()
    headers = {"User-Agent": settings.info_user_agent, "Referer": "https://t.me/"}
    # 先落临时名，content-type 解析出扩展名后再改名，避免用错后缀
    tmp = media_path(item_id, f"{index_no:03d}.part")
    digest = hashlib.sha256()
    total = 0
    with httpx.Client(
        timeout=settings.info_http_timeout_seconds, headers=headers, follow_redirects=False
    ) as client:
        response = _open_stream(client, remote_url, settings.info_max_redirects)
        try:
            if response.status_code >= 400:
                raise InfoError(
                    f"下载失败 HTTP {response.status_code}",
                    status_code=422,
                    error_type="download_failed",
                )
            content_type = _resolve_content_type(
                response.headers.get("content-type", ""), remote_url, kind
            )
            if not _allowed(content_type):
                raise InfoError(
                    f"不支持的媒体类型: {content_type}",
                    status_code=422,
                    error_type="unsupported_content_type",
                )
            length = response.headers.get("content-length")
            length_int = int(length) if length and length.isdigit() else None
            if length_int is not None and length_int > max_bytes:
                raise InfoError("媒体项超过大小上限", status_code=413, error_type="too_large")
            with tmp.open("wb") as handle:
                for chunk in response.iter_bytes(65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise InfoError("媒体项超过大小上限", status_code=413, error_type="too_large")
                    handle.write(chunk)
                    digest.update(chunk)
            if total == 0:
                raise InfoError("下载内容为空", status_code=422, error_type="download_failed")
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            response.close()

    filename = final_filename(index_no, content_type, kind)
    dest = media_path(item_id, filename)
    tmp.replace(dest)
    width, height = probe_image_size(dest)
    return FetchedFile(
        filename=filename,
        content_type=content_type,
        size_bytes=total,
        sha256=digest.hexdigest(),
        width=width,
        height=height,
    )

"""媒体转存：把上游媒体落盘到本地数据卷。

Telegram 的媒体直链（`cdn4.telesco.pe`）带签名且会过期，官方也提示要尽快下载/转存，
因此采集时一律落到 `{INFO_MEDIA_PATH}/{item_id}/{index}.{ext}`，之后只对外提供本地地址。

上游不返回图片宽高，这里在落盘后用 Pillow 探测真实尺寸写库，供前端瀑布流精确占位。
"""

from __future__ import annotations

import hashlib
import io
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


def referer_for(url: str) -> str:
    """按图片主机选择 Referer：微信图床校验来源页，缺省会返回 403。"""
    host = (urlparse(url).hostname or "").lower()
    if host == "mmbiz.qpic.cn" or host.endswith(".mmbiz.qpic.cn"):
        return "https://mp.weixin.qq.com/"
    return "https://t.me/"


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
    headers = {"User-Agent": settings.info_user_agent, "Referer": referer_for(remote_url)}
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


# ---- 上报媒体的直存（Agent 上传字节，不联网）----
# 严格白名单：只接受栅格图片与常见视频容器；显式排除 image/svg+xml——它能携带脚本，
# 经同源媒体路由回吐会形成存储型 XSS，与是否信任上传者无关。
UPLOAD_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
UPLOAD_VIDEO_TYPES = ("video/mp4", "video/webm", "video/quicktime")
UPLOAD_MEDIA_TYPES = (*UPLOAD_IMAGE_TYPES, *UPLOAD_VIDEO_TYPES)

_PILLOW_FORMAT_TO_TYPE = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}


def _image_type_from_magic(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _detect_image_type(data: bytes) -> str | None:
    """优先用 Pillow 解码校验（确认是真实栅格图），Pillow 缺失时退回魔数。"""
    magic = _image_type_from_magic(data)
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            fmt = (image.format or "").lower()
        detected = _PILLOW_FORMAT_TO_TYPE.get(fmt)
        if detected is not None:
            return detected
    except ImportError:
        return magic
    except Exception:
        return None
    return magic


def _detect_video_type(data: bytes) -> str | None:
    if len(data) < 12:
        return None
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand[:2] == b"qt":
            return "video/quicktime"
        return "video/mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    return None


def detect_upload_type(data: bytes, *, as_video: bool = False) -> str | None:
    """判定上传字节的真实媒体类型；不支持时返回 None。"""
    if as_video:
        return _detect_video_type(data) or _detect_image_type(data)
    return _detect_image_type(data) or _detect_video_type(data)


def store_uploaded_media(
    item_id: str,
    *,
    index_no: int,
    content_type: str,
    data: bytes,
    kind: str = "",
) -> FetchedFile:
    """把 Agent 上传的媒体字节直写条目目录；全程不发任何网络请求。

    与 `fetch_media` 的差异：无重定向与 `MEDIA_HOSTS` 校验（没有远程请求），改为严格
    类型白名单 + 字节解码校验；入库 `content_type` 采用检测结果而非客户端提交值。
    """
    if not data:
        raise InfoError("媒体内容为空", status_code=400, error_type="invalid_request")
    declared = (content_type or "").split(";")[0].strip().lower()
    as_video = declared.startswith("video/") or kind == "video"
    detected = detect_upload_type(data, as_video=as_video)
    if detected is None:
        raise InfoError(
            "不支持的媒体类型（仅支持 jpg/png/webp/gif 图片与 mp4/webm/mov 视频）",
            status_code=400,
            error_type="unsupported_content_type",
        )
    resolved_kind = "video" if detected.startswith("video/") else "image"
    dest_name = final_filename(index_no, detected, resolved_kind)

    tmp = media_path(item_id, f"{index_no:03d}.part")
    dest = media_path(item_id, dest_name)
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    width, height = probe_image_size(dest) if resolved_kind == "image" else (None, None)
    return FetchedFile(
        filename=dest_name,
        content_type=detected,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        width=width,
        height=height,
    )

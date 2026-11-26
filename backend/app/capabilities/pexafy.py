from __future__ import annotations

import asyncio
import base64
import binascii
import json
import secrets
from urllib.parse import quote, urljoin, urlsplit

import httpx
from jsonschema import Draft202012Validator
from sqlalchemy import func, select

from app.capabilities.base import CallContext, CapabilityError, CapabilitySpec, McpToolDef
from app.clock import utcnow
from app.config import get_settings
from app.crypto import decrypt_secret
from app.info.urlguard import assert_safe_url
from app.models import ExternalMcpArtifact, PexafySettings
from app.services import external_mcp
from app.services import external_mcp_artifacts as files

BASE_URL = "https://api.pexafy.com/api/v1"
MAX_IMAGE = 10 * 1024 * 1024
MAX_JSON = 14 * 1024 * 1024
SIZES = ["thumb", "small", "regular", "large", "full"]
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/avif": "avif"}
# Only known stock-photo provider domains, including their CDN subdomains.
HOSTS = (
    "pexels.com",
    "unsplash.com",
    "pixabay.com",
    "kaboompics.com",
    "burst.shopify.com",
    "cdn.shopify.com",
    "shopifycdn.com",
    "stocksnap.io",
    "picjumbo.com",
    "skitterphoto.com",
    "negativespace.co",
    "pexafy.com",
)


def credential(db):
    row = db.get(PexafySettings, 1)
    if not row or not row.api_key_encrypted:
        raise CapabilityError("请先配置 Pexafy API Key", 503, "not_configured")
    try:
        return decrypt_secret(row.api_key_encrypted, get_settings().app_secret_key)
    except ValueError:
        raise CapabilityError("Pexafy 密钥无法解密，请重新配置", 503, "not_configured") from None


async def api_call(key, method, path, *, params=None, upload=None):
    try:
        async with (
            asyncio.timeout(40),
            httpx.AsyncClient(timeout=30, follow_redirects=False) as client,
            client.stream(method, BASE_URL + path, params=params, files=upload, headers={"x-api-key": key}) as response,
        ):
            if not response.is_success:
                status = response.status_code
                raise CapabilityError(
                    f"Pexafy 请求失败（HTTP {status}）",
                    status if status in (400, 402, 403, 404, 422, 429) else 502,
                    "upstream_error",
                )
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > 8 * 1024 * 1024:
                    raise CapabilityError("Pexafy 响应过大", 502, "upstream_error")
                data.extend(chunk)
        result = json.loads(data)
        if not isinstance(result, dict) or result.get("success") is not True:
            raise ValueError
        return result
    except (httpx.TimeoutException, TimeoutError):
        raise CapabilityError("Pexafy 请求超时", 504, "upstream_error") from None
    except (httpx.HTTPError, ValueError, RecursionError):
        raise CapabilityError("Pexafy 响应无效或连接失败", 502, "upstream_error") from None


def string(description, **kw):
    return {"type": "string", "description": description, **kw}


def integer(description, minimum, maximum):
    return {"type": "integer", "description": description, "minimum": minimum, "maximum": maximum}


def number(description, minimum, maximum):
    return {"type": "number", "description": description, "minimum": minimum, "maximum": maximum}


def array(description):
    return {"type": "array", "items": {"type": "string"}, "description": description}


PHOTO = string("Pexafy 图片 ID", minLength=1, maxLength=255, pattern=r"^[a-zA-Z0-9_-]+$")
PAGE = {"per_page": integer("每页数量", 1, 100), "limit": integer("总结果上限", 1, 100), "cursor": string("下一页游标")}
FILTERS = {
    "q": string("自然语言描述，支持中文", maxLength=500),
    "color_name": string("颜色名称"),
    "color_hex": string("HEX 色值"),
    "color_tolerance": integer("颜色容差", 0, 255),
    "orientation": array("landscape、portrait、square"),
    "source": array("来源平台"),
    "license_type": array("许可证类型"),
    "score_threshold": number("最低相关度", 0, 1),
    "after_date": string("发布日期 YYYY-MM-DD"),
    "sort_by": string("排序", enum=["relevance", "newest"]),
    "photographer": string("摄影师用户名"),
    **PAGE,
}


def tool(name, description, properties=None, required=()):
    return McpToolDef(
        "pexafy_" + name,
        description,
        {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False},
        operation=name,
        returns="图片及来源、授权信息；下载工具返回网关签名下载地址。",
    )


TOOLS = [
    tool("search_photos", "语义搜索图片，可用于 PPT 配图", {**FILTERS, "fields": string("返回字段")}),
    tool(
        "search_by_image",
        "按图片 ID 或 Base64 图片搜索，可结合文字；翻页仅需 cursor",
        {
            **FILTERS,
            "photo_id": PHOTO,
            "image_base64": string("JPEG/PNG/WebP/AVIF 原始 Base64", maxLength=MAX_JSON),
            "text_alpha": number("文字权重，默认 1.7", 0, 10),
        },
    ),
    tool("get_photo", "获取图片详情及高清地址", {"photo_id": PHOTO, "fields": string("返回字段")}, ["photo_id"]),
    tool("similar_photos", "寻找相似图片", {"photo_id": PHOTO, **PAGE}, ["photo_id"]),
    tool(
        "download_photo",
        "XAPI 下载图片并返回有效期 15 分钟的签名链接",
        {"photo_id": PHOTO, "size": string("图片尺寸，默认 large", enum=SIZES)},
        ["photo_id"],
    ),
    *[
        tool("list_" + name, "获取" + label + "列表")
        for name, label in [("colors", "颜色"), ("sources", "来源"), ("orientations", "方向"), ("licenses", "许可证")]
    ],
]


def image_type(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis"):
        return "image/avif"
    raise CapabilityError("不支持的图片格式", 422, "invalid_image")


async def fetch_image(url):
    try:
        async with (
            asyncio.timeout(45),
            httpx.AsyncClient(timeout=25, follow_redirects=False, trust_env=False) as client,
        ):
            for _ in range(4):
                parsed = urlsplit(url)
                if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
                    raise ValueError
                await asyncio.to_thread(assert_safe_url, url, allow_hosts=HOSTS)
                async with client.stream("GET", url) as response:
                    if response.is_redirect and response.headers.get("location"):
                        url = urljoin(url, response.headers["location"])
                        continue
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > files.MAX_SIZE:
                            raise CapabilityError("图片超过下载大小上限", 413, "payload_too_large")
                        data.extend(chunk)
                    kind = image_type(data)
                    return bytes(data), kind
    except CapabilityError:
        raise
    except Exception:
        raise CapabilityError("图片下载失败或来源不受支持", 502, "download_error") from None
    raise CapabilityError("图片重定向次数过多", 502, "download_error")


def publish_image(data, kind):
    try:
        base = files.public_base()
    except ValueError:
        raise CapabilityError("请配置有效的 APP_BASE_URL", 503, "artifact_unavailable") from None
    identifier = secrets.token_hex(16)
    path = files.file_path(identifier)
    try:
        with external_mcp.write_session() as db:
            used = db.scalar(select(func.coalesce(func.sum(ExternalMcpArtifact.size_bytes), 0)))
            if used + len(data) > files.STORAGE_QUOTA:
                raise CapabilityError("临时文件存储已满", 429, "artifact_storage_limit")
            row = ExternalMcpArtifact(
                id=identifier,
                call_id=identifier,
                dispatch_id=identifier,
                filename=f"photo.{IMAGE_TYPES[kind]}",
                content_type=kind,
                size_bytes=len(data),
                upload_digest=secrets.token_hex(32),
                state="published",
                expires_at=utcnow() + files.DOWNLOAD_TTL,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            db.add(row)
            result = files.delivery_metadata(row, base)
        return result
    except Exception:
        path.unlink(missing_ok=True)
        raise


class PexafyProvider:
    spec = CapabilitySpec(
        "pexafy",
        "Pexafy 图片搜索",
        "语义搜索、相似图片及 XAPI 高清素材下载，适用于 PPT 配图。",
        category="media",
        admin_path="/mcp-plaza/pexafy",
        icon="image",
        integration={
            "rest_endpoints": [
                {
                    "method": "POST",
                    "path": "/v1/capabilities/pexafy/" + item.operation,
                    "summary": item.description,
                    "content_type": "application/json",
                    "example_body": (
                        {"photo_id": "PHOTO_ID"}
                        if "photo_id" in item.input_schema["properties"]
                        else {"q": "现代办公室，横向构图"}
                        if item.operation == "search_photos"
                        else {}
                    ),
                }
                for item in TOOLS
            ],
            "notes": [
                "图片下载链接有效期 15 分钟，请及时保存。",
                "图片上传使用 Base64，最大 10 MB；所有授权客户端共享上游额度。",
            ],
        },
    )

    def list_mcp_tools(self):
        return TOOLS

    async def dispatch(self, operation, payload, ctx: CallContext):
        definition = next((item for item in TOOLS if item.operation == operation), None)
        if definition is None:
            raise CapabilityError("未知 Pexafy 操作", 404, "not_found")
        if not Draft202012Validator(definition.input_schema).is_valid(payload):
            raise CapabilityError("Pexafy 参数无效", 422, "invalid_request")
        if payload.get("color_name") and payload.get("color_hex"):
            raise CapabilityError("color_name 与 color_hex 不可同时使用", 422, "invalid_request")
        key = credential(ctx.db)
        ctx.db.commit()
        params = dict(payload)
        if operation == "search_photos":
            return await api_call(key, "GET", "/search/photos", params=params)
        if operation == "search_by_image":
            encoded = params.pop("image_base64", None)
            if (encoded and params.get("photo_id")) or not (encoded or params.get("photo_id") or params.get("cursor")):
                raise CapabilityError("请传入图片 ID、Base64 图片或分页游标，图片 ID 与上传互斥", 422)
            upload = None
            if encoded:
                try:
                    data = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error):
                    raise CapabilityError("无效的 Base64 图片", 422) from None
                if len(data) > MAX_IMAGE:
                    raise CapabilityError("图片超过 10 MB", 413)
                kind = image_type(data)
                upload = {"image": (f"image.{IMAGE_TYPES[kind]}", data, kind)}
            return await api_call(key, "POST", "/search/photos", params=params, upload=upload)
        if operation.startswith("list_"):
            return await api_call(key, "GET", "/facets/" + operation.removeprefix("list_"))
        photo_id = params.pop("photo_id")
        path = "/photos/" + quote(photo_id, safe="")
        if operation == "similar_photos":
            return await api_call(key, "GET", path + "/similar", params=params)
        if operation == "get_photo":
            return await api_call(key, "GET", path, params=params)
        photo = (await api_call(key, "GET", path)).get("data") or {}
        size = params.get("size", "large")
        url = (photo.get("urls") or {}).get(size)
        if not isinstance(url, str) or not url:
            raise CapabilityError("该图片没有指定尺寸的地址", 404, "not_found")
        data, kind = await fetch_image(url)
        artifact = await asyncio.to_thread(publish_image, data, kind)
        return {
            **artifact,
            "url": artifact["download_url"],
            "photo_id": photo_id,
            "size": size,
            "original_width": photo.get("width"),
            "original_height": photo.get("height"),
            **{
                field: photo.get(field)
                for field in ("source", "license_type", "photographer_full_name", "attribution", "source_image_url")
            },
        }

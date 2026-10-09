"""Microsoft Store：解析产品与安装包列表，并在允许的域名内代理 HTTP 下载。"""

from __future__ import annotations

import json
import os
import re
import tempfile
from urllib.parse import parse_qs, quote, urlparse

from app.offline.errors import OfflineError, UpstreamError
from app.offline.http import LAYER_TIMEOUT, make_client

RG_FILES_URL = "https://store.rg-adguard.net/api/GetFiles"
DISPLAY_CATALOG_URL = "https://displaycatalog.mp.microsoft.com/v7.0/products"

_VALID_RINGS = {"WIF", "WIS", "RP", "Retail"}
_ALLOWED_HTTP_HOST_RE = re.compile(
    r"(?:^|\.)(download\.microsoft\.com|dl\.delivery\.mp\.microsoft\.com)$", re.IGNORECASE
)
_ROW_RE = re.compile(r"<tr[^>]*>([\s\S]*?)</tr>", re.IGNORECASE)
_LINK_RE = re.compile(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', re.IGNORECASE)
_TD_RE = re.compile(r"<td[^>]*>([\s\S]*?)</td>", re.IGNORECASE)
_CATEGORY_RE = re.compile(r"CategoryID:\s*</b>\s*<i>([^<]+)</i>", re.IGNORECASE)


def _is_display_catalog_big_id(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9]{12}$", value))


def _is_store_identifier(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9]{12,16}$", value))


def _extract_store_identifier_from_url(raw: str) -> str | None:
    trimmed = (raw or "").strip()
    if _is_store_identifier(trimmed):
        return trimmed.upper()
    parsed = urlparse(trimmed)
    if not parsed.scheme and not parsed.netloc:
        return None
    params = parse_qs(parsed.query)
    for key in ("productId", "productid", "itemId", "itemid"):
        value = (params.get(key) or [""])[0]
        if _is_store_identifier(value):
            return value.upper()
    for pattern in (
        r"/detail/([A-Za-z0-9]{12,16})(?:[/?#]|$)",
        r"/store/productid/([A-Za-z0-9]{12,16})(?:[/?#]|$)",
        r"/productid/([A-Za-z0-9]{12,16})(?:[/?#]|$)",
    ):
        matched = re.search(pattern, parsed.path, re.IGNORECASE)
        if matched:
            return matched.group(1).upper()
    return None


def _normalize_store_identifier(request_type: str, query: str) -> str:
    trimmed = (query or "").strip()
    if not trimmed:
        raise OfflineError("query 不能为空")
    if request_type == "url":
        if not _extract_store_identifier_from_url(trimmed):
            raise OfflineError("无法从 URL 提取应用标识")
        return trimmed
    if request_type == "ProductId":
        if not _is_display_catalog_big_id(trimmed):
            raise OfflineError("无效 ProductId（需 12 位，例如 9N0DX20HK701）")
        return trimmed.upper()
    if request_type == "PackageFamilyName":
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*_[A-Za-z0-9]+$", trimmed):
            raise OfflineError("无效 PackageFamilyName")
        return trimmed
    if request_type == "CategoryId":
        if not re.match(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", trimmed):
            raise OfflineError("无效 CategoryId")
        return trimmed.lower()
    raise OfflineError("不支持的请求类型")


def _display_catalog_big_id(request_type: str, query: str) -> str | None:
    if request_type == "ProductId":
        return query
    if request_type == "url":
        store_id = _extract_store_identifier_from_url(query)
        return store_id if store_id and _is_display_catalog_big_id(store_id) else None
    return None


def _rg_type(request_type: str) -> str:
    return "CategoryID" if request_type == "CategoryId" else request_type


def _decode_html(value: str) -> str:
    return (
        value.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _decode_html(value))).strip()


def _normalize_rg_language(language: str) -> str:
    base, _, region = language.partition("-")
    if not base or not region:
        return language
    return f"{base.lower()}-{region.upper()}"


def _parse_rg_html(html: str) -> dict:
    category_match = _CATEGORY_RE.search(html)
    files: list[dict] = []
    for row_match in _ROW_RE.finditer(html):
        row_html = row_match.group(1) or ""
        link_match = _LINK_RE.search(row_html)
        if not link_match:
            continue
        cells = [text for text in (_strip_html(cell) for cell in _TD_RE.findall(row_html)) if text]
        expires = cells[1] if len(cells) >= 4 else ""
        if len(cells) >= 4:
            sha1 = cells[-2] if len(cells) >= 2 else ""
            size = cells[-1]
        elif len(cells) >= 2:
            sha1 = cells[-1]
            size = ""
        else:
            sha1 = ""
            size = ""
        files.append(
            {
                "url": _decode_html(link_match.group(1)),
                "name": _decode_html(link_match.group(2)),
                "expires": expires,
                "sha1": sha1,
                "size": size,
            }
        )
    return {"categoryId": category_match.group(1).strip() if category_match else None, "files": files}


async def _fetch_rg_files(request_type: str, query: str, ring: str, language: str) -> dict:
    payload = {
        "type": _rg_type(request_type),
        "url": query,
        "ring": ring,
        "lang": _normalize_rg_language(language),
    }
    async with make_client() as client:
        response = await client.post(
            RG_FILES_URL,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://store.rg-adguard.net",
                "Referer": "https://store.rg-adguard.net/",
            },
        )
    if response.status_code >= 400:
        raise UpstreamError(f"下载接口响应错误: {response.status_code}", status_code=response.status_code)
    parsed = _parse_rg_html(response.text)
    if not parsed["files"]:
        raise UpstreamError("下载接口未返回可用文件")
    return parsed


def _safe_json(raw: object) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


async def resolve(request_type: str, query: str, market: str = "US", language: str = "en-us", ring: str = "RP") -> dict:
    market = (market or "US").upper()
    language = (language or "en-us").lower()
    ring = ring or "RP"
    if not re.match(r"^[A-Z]{2}$", market):
        raise OfflineError("无效市场代码")
    if not re.match(r"^[a-z]{2,3}(?:-[a-z]{2})?$", language):
        raise OfflineError("无效语言代码")
    if ring not in _VALID_RINGS:
        raise OfflineError("无效 ring 参数")

    store_identifier = _normalize_store_identifier(request_type, query)

    files = None
    files_error = None
    category_id = None
    try:
        rg = await _fetch_rg_files(request_type, store_identifier, ring, language)
        files = rg["files"]
        category_id = rg["categoryId"]
    except Exception as error:  # noqa: BLE001 - 单侧失败不影响另一个来源
        files_error = str(error)

    product = None
    big_id = _display_catalog_big_id(request_type, store_identifier)
    if big_id:
        url = (
            f"{DISPLAY_CATALOG_URL}?bigIds={quote(big_id)}"
            f"&market={quote(market)}&languages={quote(language)}"
        )
        async with make_client() as client:
            upstream = await client.get(url, headers={"Accept-Language": f"{language},en-us;q=0.8"})
        if upstream.status_code < 400:
            try:
                data = upstream.json()
            except ValueError:
                data = {}
            products = data.get("Products") if isinstance(data, dict) else None
            product = products[0] if products else None

    if not product and not files:
        raise OfflineError(files_error or "未找到对应产品，请检查 ProductId 或链接", status_code=404)

    localized_list = (product or {}).get("LocalizedProperties") or []
    product_properties = (product or {}).get("Properties") or {}
    localized = next(
        (item for item in localized_list if str(item.get("Language", "")).lower() == language),
        localized_list[0] if localized_list else {},
    )

    skus = []
    for entry in (product or {}).get("DisplaySkuAvailabilities") or []:
        sku = entry.get("Sku") or {}
        properties = sku.get("Properties") or {}
        availability = (entry.get("Availabilities") or [{}])[0]
        packages = [
            {
                "packageFullName": str(pkg.get("PackageFullName", "")),
                "packageId": str(pkg.get("PackageId", "")),
                "packageFamilyName": str(pkg.get("PackageFamilyName", "")),
                "packageFormat": str(pkg.get("PackageFormat", "")),
                "version": str(pkg.get("Version", "")),
                "architectures": [str(a) for a in pkg.get("Architectures", [])] if isinstance(pkg.get("Architectures"), list) else [],
                "maxDownloadSizeInBytes": int(pkg.get("MaxDownloadSizeInBytes") or 0),
                "maxInstallSizeInBytes": int(pkg.get("MaxInstallSizeInBytes") or 0),
                "hash": str(pkg.get("Hash", "")),
                "contentId": str(pkg.get("ContentId", "")),
                "packageUri": str(pkg.get("PackageUri", "")),
                "packageDownloadUris": [str(u) for u in pkg["PackageDownloadUris"]] if isinstance(pkg.get("PackageDownloadUris"), list) else None,
            }
            for pkg in (properties.get("Packages") or [])
        ]
        fulfillment = _safe_json(properties.get("FulfillmentData"))
        skus.append(
            {
                "skuId": str(sku.get("SkuId", "")),
                "skuType": str(sku.get("SkuType", "")),
                "actions": [str(a) for a in availability.get("Actions", [])] if isinstance(availability.get("Actions"), list) else [],
                "availabilityId": str(availability.get("AvailabilityId", "")),
                "fulfillmentData": fulfillment,
                "packages": packages,
            }
        )

    return {
        "productId": str((product or {}).get("ProductId") or _extract_store_identifier_from_url(store_identifier) or store_identifier),
        "title": str(localized.get("ProductTitle", "")),
        "publisherName": str(localized.get("PublisherName", "")),
        "description": str(localized.get("ProductDescription", "")),
        "lastModifiedDate": (product or {}).get("LastModifiedDate"),
        "packageFamilyNames": [str(p) for p in product_properties.get("PackageFamilyNames", [])] if isinstance(product_properties.get("PackageFamilyNames"), list) else [],
        "market": market,
        "language": language,
        "categoryId": category_id,
        "files": files,
        "filesSource": "rg-adguard" if files else None,
        "filesError": files_error,
        "skus": skus,
    }


def _sanitize_filename(value: str) -> str:
    return re.sub(r"[\\/]", "_", re.sub(r'[\r\n"]', "", value or "")).strip()


def _infer_filename(url: str) -> str:
    last = urlparse(url).path.rsplit("/", 1)[-1] or "download.bin"
    return _sanitize_filename(last) or "download.bin"


async def fetch_to_file(raw_url: str, filename: str = "", on_progress=None) -> tuple[str, str, str]:
    """把允许的微软下载链接流式存到临时文件，返回 (临时路径, 文件名, content-type)。"""
    parsed = urlparse((raw_url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise OfflineError("仅代理 HTTP/HTTPS 下载链接")
    if not _ALLOWED_HTTP_HOST_RE.search(parsed.hostname or ""):
        raise OfflineError("不允许代理该下载域名")
    safe_name = _sanitize_filename(filename) or _infer_filename(raw_url)
    with tempfile.NamedTemporaryFile(prefix="offline-msstore-", suffix=os.path.splitext(safe_name)[1] or ".bin", delete=False) as tmp:
        path = tmp.name
    content_type = "application/octet-stream"
    try:
        async with make_client(timeout=LAYER_TIMEOUT, follow_redirects=True) as client, client.stream("GET", raw_url) as response:
            if response.status_code >= 400:
                raise UpstreamError(f"上游下载失败: {response.status_code}", status_code=response.status_code)
            content_type = response.headers.get("Content-Type") or content_type
            expected = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(path, "wb") as handle:
                async for chunk in response.aiter_bytes():
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        on_progress(downloaded, expected)
    except Exception:
        if os.path.exists(path):
            os.unlink(path)
        raise
    return path, safe_name, content_type

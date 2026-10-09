"""Microsoft Edge 扩展：输入解析、搜索、详情与 .crx / .zip 下载。"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from app.offline.crx import crx_to_zip
from app.offline.errors import OfflineError, UpstreamError
from app.offline.http import make_client

EDGE_MARKET = "US"
EDGE_LANGUAGE = "en-US"

_CRX_ID_RE = re.compile(r"^[a-z]{32}$")
_STORE_PRODUCT_ID_RE = re.compile(r"^[A-Za-z0-9]{12}$")

_DETAIL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)


def parse_query(query: str) -> dict | None:
    """解析 crxId（32 位小写）或 storeProductId（12 位）。"""
    trimmed = (query or "").strip()
    if not trimmed:
        return None
    lowered = trimmed.lower()
    if _CRX_ID_RE.match(lowered):
        return {"type": "crxId", "value": lowered}
    if _STORE_PRODUCT_ID_RE.match(trimmed):
        return {"type": "storeProductId", "value": trimmed.upper()}

    parsed = None
    for candidate in (trimmed, f"https://{trimmed}"):
        try:
            parsed = urlparse(candidate)
            break
        except ValueError:
            parsed = None
    if parsed is None or not parsed.netloc:
        return None

    found = re.search(r"([a-z]{32})", parsed.geturl().lower())
    if found:
        return {"type": "crxId", "value": found.group(1)}

    params = parse_qs(parsed.query)
    for key in ("productId", "productid", "itemId", "itemid"):
        value = (params.get(key) or [""])[0]
        if _STORE_PRODUCT_ID_RE.match(value):
            return {"type": "storeProductId", "value": value.upper()}
    return None


def _detail_url(parsed: dict) -> str:
    if parsed["type"] == "crxId":
        return f"https://microsoftedge.microsoft.com/addons/getproductdetailsbycrxid/{parsed['value']}"
    return f"https://microsoftedge.microsoft.com/addons/getproductdetails/{parsed['value']}"


async def search(query: str) -> list[dict]:
    keyword = (query or "").strip()
    if not keyword:
        raise OfflineError("缺少搜索关键词")
    url = "https://microsoftedge.microsoft.com/addons/v4/getfilteredorderedsearch"
    params = {
        "hl": EDGE_LANGUAGE,
        "gl": EDGE_MARKET,
        "Query": keyword,
        "pgNo": "1",
        "filteredCategories": "Edge-Extensions",
        "filteredAddon": "1",
        "filterFeaturedAddons": "false",
        "filteredRating": "0",
        "sortBy": "Relevance",
    }
    async with make_client() as client:
        response = await client.get(url, params=params, headers={"Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"})
    if response.status_code >= 400:
        raise UpstreamError(f"Edge Add-ons 响应错误: {response.status_code}", status_code=response.status_code)
    try:
        data = response.json()
    except ValueError as error:
        raise UpstreamError("Edge Add-ons 返回了非 JSON 响应") from error

    items = data.get("extensionList") if isinstance(data, dict) else None
    results: list[dict] = []
    for item in items or []:
        crx_id = item.get("crxId")
        name = item.get("name")
        if not isinstance(crx_id, str) or not isinstance(name, str):
            continue
        results.append(
            {
                "id": crx_id,
                "storeProductId": item.get("storeProductId"),
                "name": name,
                "developer": item.get("developerName"),
                "description": item.get("shortDescription"),
                "iconUrl": item.get("logoUrl"),
            }
        )
        if len(results) >= 10:
            break
    return results


async def detail(query: str) -> dict:
    parsed = parse_query(query)
    if parsed is None:
        raise OfflineError("无效的 Edge 扩展 ID、ProductId 或商店链接")
    url = _detail_url(parsed)
    params = {"hl": EDGE_LANGUAGE, "gl": EDGE_MARKET}
    async with make_client() as client:
        response = await client.get(url, params=params, headers={"Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"})
    if response.status_code >= 400:
        raise UpstreamError(f"Edge Add-ons 响应错误: {response.status_code}", status_code=response.status_code)
    try:
        data = response.json()
    except ValueError as error:
        raise UpstreamError("Edge Add-ons 返回了非 JSON 响应") from error
    if not isinstance(data, dict):
        raise OfflineError("未找到扩展信息", status_code=404)
    return data


async def fetch_crx(extension_id: str) -> bytes:
    normalized = (extension_id or "").strip().lower()
    if not _CRX_ID_RE.match(normalized):
        raise OfflineError("无效的 Edge 扩展 ID 格式")
    url = "https://edge.microsoft.com/extensionwebstorebase/v1/crx"
    params = {"response": "redirect", "x": f"id={normalized}&installsource=ondemand&uc"}
    async with make_client(timeout=120.0) as client:
        response = await client.get(url, params=params, headers={"User-Agent": _DETAIL_UA})
    if response.status_code >= 400:
        raise UpstreamError(f"Edge 下载服务响应错误: {response.status_code}", status_code=response.status_code)
    data = response.content
    if not data:
        raise OfflineError("未获取到扩展文件，该扩展可能已下架或不可用", status_code=404)
    return data


async def download(extension_id: str, fmt: str = "crx", on_progress=None) -> tuple[bytes, str, str]:
    normalized = (extension_id or "").strip().lower()
    data = await fetch_crx(normalized)
    if on_progress:
        on_progress(len(data), len(data))
    if (fmt or "crx").lower() == "zip":
        data = crx_to_zip(data)
        return data, f"{normalized}.zip", "application/zip"
    return data, f"{normalized}.crx", "application/x-chrome-extension"

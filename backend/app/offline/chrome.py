"""Chrome 扩展：搜索、详情与 .crx / .zip 下载。"""

from __future__ import annotations

import re

from app.offline.crx import crx_to_zip
from app.offline.errors import OfflineError, UpstreamError
from app.offline.http import make_client

ID_RE = re.compile(r"^[a-z]{32}$")
_SEARCH_ENTRY_RE = re.compile(r"detail/([^/]+)/([a-z]{32})")
_TITLE_RE = re.compile(r"<title>(.+?)\s*[-–—]\s*Chrome[^<]*</title>")
_DESC_RE = re.compile(r'meta\s+name="description"\s+content="([^"]*)"')
_OG_IMAGE_RE = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE)

_DOWNLOAD_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def normalize_id(value: str) -> str:
    candidate = (value or "").strip().lower()
    if ID_RE.match(candidate):
        return candidate
    found = re.search(r"([a-z]{32})", candidate)
    if found:
        return found.group(1)
    raise OfflineError("无效的 Chrome 扩展 URL 或 ID")


async def search(query: str) -> list[dict]:
    keyword = (query or "").strip()
    if not keyword:
        raise OfflineError("缺少搜索关键词")
    url = f"https://chromewebstore.google.com/search/{keyword}"
    async with make_client() as client:
        response = await client.get(url, headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    if response.status_code >= 400:
        raise UpstreamError(f"Chrome Web Store 响应错误: {response.status_code}", status_code=response.status_code)

    results: list[dict] = []
    seen: set[str] = set()
    for match in _SEARCH_ENTRY_RE.finditer(response.text):
        slug, extension_id = match.group(1), match.group(2)
        if extension_id in seen:
            continue
        seen.add(extension_id)
        name = " ".join(word[:1].upper() + word[1:] for word in slug.split("-"))
        results.append({"id": extension_id, "name": name})
        if len(results) >= 10:
            break
    return results


async def detail(extension_id: str) -> dict:
    normalized = normalize_id(extension_id)
    url = f"https://chromewebstore.google.com/detail/{normalized}"
    async with make_client() as client:
        response = await client.get(url, headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    if response.status_code >= 400:
        raise UpstreamError(f"Chrome Web Store 响应错误: {response.status_code}", status_code=response.status_code)
    title = _TITLE_RE.search(response.text)
    desc = _DESC_RE.search(response.text)
    icon = _OG_IMAGE_RE.search(response.text)
    return {
        "id": normalized,
        "name": title.group(1).strip() if title else None,
        "description": desc.group(1).strip() if desc else None,
        "icon": icon.group(1).strip() if icon else None,
    }


async def fetch_crx(extension_id: str) -> bytes:
    normalized = normalize_id(extension_id)
    params = {
        "response": "redirect",
        "os": "win",
        "arch": "x64",
        "os_arch": "x86_64",
        "nacl_arch": "x86-64",
        "prod": "chromecrx",
        "prodchannel": "beta",
        "prodversion": "131.0.6778.86",
        "lang": "zh-CN",
        "acceptformat": "crx2,crx3",
        "x": f"id={normalized}&installsource=ondemand&uc",
    }
    url = "https://clients2.google.com/service/update2/crx"
    async with make_client(timeout=120.0) as client:
        response = await client.get(url, params=params, headers={"User-Agent": _DOWNLOAD_UA})
    if response.status_code >= 400:
        raise UpstreamError(f"Chrome 服务器响应错误: {response.status_code}", status_code=response.status_code)
    data = response.content
    if not data:
        raise OfflineError("未获取到扩展文件，该扩展可能已下架或不可用", status_code=404)
    return data


async def download(extension_id: str, fmt: str = "crx") -> tuple[bytes, str, str]:
    """返回 (字节, 文件名, content-type)。"""
    normalized = normalize_id(extension_id)
    data = await fetch_crx(normalized)
    if (fmt or "crx").lower() == "zip":
        data = crx_to_zip(data)
        return data, f"{normalized}.zip", "application/zip"
    return data, f"{normalized}.crx", "application/x-chrome-extension"


__all__ = ["normalize_id", "search", "detail", "fetch_crx", "download"]

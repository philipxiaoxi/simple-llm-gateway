"""VSCode 插件：查询版本并生成 .vsix 直链。"""

from __future__ import annotations

import os
import re
import tempfile
from urllib.parse import parse_qs, urlparse

from app.offline.errors import OfflineError, UpstreamError
from app.offline.http import LAYER_TIMEOUT, make_client, post_json

MARKETPLACE_QUERY_URL = "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"
MAX_VERSIONS = 20

_ITEM_NAME_RE = re.compile(r"^[A-Za-z0-9][\w-]*\.[A-Za-z0-9][\w-]*$")


def parse_item_name(query: str) -> tuple[str, str]:
    """从 Marketplace 链接或 `publisher.extension` 中解析出插件标识。"""
    raw = (query or "").strip()
    if not raw:
        raise OfflineError("请输入插件链接或 publisher.extension")

    item_name = raw
    if "://" in raw or raw.startswith("marketplace.visualstudio.com"):
        url = raw if "://" in raw else f"https://{raw}"
        parsed = urlparse(url)
        item_name = (parse_qs(parsed.query).get("itemName") or [""])[0]
        if not item_name:
            raise OfflineError("无效的插件 URL，示例：https://marketplace.visualstudio.com/items?itemName=publisher.extension")
    elif "itemName=" in raw:
        item_name = raw.split("itemName=", 1)[1].split("&", 1)[0]

    item_name = item_name.strip()
    if "." not in item_name:
        raise OfflineError("无效的插件 ID 格式，应为 publisher.extension")
    if not _ITEM_NAME_RE.match(item_name):
        raise OfflineError("无效的插件 ID 格式，应为 publisher.extension")
    publisher, extension = item_name.rsplit(".", 1)
    return publisher, extension


def vsix_url(publisher: str, extension: str, version: str) -> str:
    return (
        "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/"
        f"{publisher}/vsextensions/{extension}/{version}/vspackage"
    )


async def query(raw: str) -> dict:
    publisher, extension = parse_item_name(raw)
    item_name = f"{publisher}.{extension}"
    body = {
        "filters": [
            {
                "criteria": [{"filterType": 7, "value": item_name}],
                "pageNumber": 1,
                "pageSize": 1,
                "sortBy": 0,
                "sortOrder": 0,
            }
        ],
        "flags": 1,
    }
    data = await post_json(
        MARKETPLACE_QUERY_URL,
        json_body=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json;api-version=3.0-preview.1",
        },
    )
    results = (data.get("results") or [{}])[0]
    extensions = results.get("extensions") or []
    if not extensions:
        raise OfflineError("未找到该插件，请检查链接是否正确", status_code=404)

    versions: list[str] = []
    seen: set[str] = set()
    for entry in extensions[0].get("versions") or []:
        version = str(entry.get("version") or "").strip()
        if version and version not in seen:
            seen.add(version)
            versions.append(version)
        if len(versions) >= MAX_VERSIONS:
            break

    extension_meta = extensions[0]
    return {
        "publisher": publisher,
        "extension": extension,
        "item_name": item_name,
        "display_name": extension_meta.get("displayName") or item_name,
        "versions": versions,
        "download_url_template": (
            "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/"
            f"{publisher}/vsextensions/{extension}/{{version}}/vspackage"
        ),
    }


async def download_vsix(publisher: str, extension: str, version: str) -> tuple[str, str]:
    """下载 .vsix 到临时文件，返回 (临时路径, 文件名)。"""
    if not version.strip():
        raise OfflineError("缺少版本号")
    url = vsix_url(publisher, extension, version.strip())
    with tempfile.NamedTemporaryFile(prefix="offline-vsix-", suffix=".vsix", delete=False) as tmp:
        path = tmp.name
    try:
        async with make_client(timeout=LAYER_TIMEOUT) as client, client.stream("GET", url) as response:
            if response.status_code >= 400:
                raise UpstreamError(f"Marketplace 下载失败: {response.status_code}", status_code=response.status_code)
            with open(path, "wb") as handle:
                async for chunk in response.aiter_bytes():
                    handle.write(chunk)
    except Exception:
        if os.path.exists(path):
            os.unlink(path)
        raise
    return path, f"{publisher}.{extension}-{version}.vsix"

"""离线下载的共享 HTTP 客户端与常量。"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.offline.errors import UpstreamError

DEFAULT_TIMEOUT = 30.0
DOCKER_TIMEOUT = 60.0
LAYER_TIMEOUT = 600.0

# 各上游大多按浏览器 UA 分发内容，统一使用配置里的 UA（与资讯采集一致）。
def user_agent() -> str:
    return get_settings().info_user_agent


def make_client(*, timeout: float = DEFAULT_TIMEOUT, follow_redirects: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=follow_redirects,
        headers={"User-Agent": user_agent()},
    )


async def get_json(url: str, *, params: dict | None = None, headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    async with make_client(timeout=timeout) as client:
        response = await client.get(url, params=params, headers=headers)
    if response.status_code >= 400:
        raise UpstreamError(f"上游响应错误: {response.status_code}", status_code=response.status_code)
    try:
        return response.json()
    except ValueError as error:
        raise UpstreamError("上游返回了非 JSON 响应") from error


async def post_json(url: str, *, json_body: dict, headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    async with make_client(timeout=timeout) as client:
        response = await client.post(url, json=json_body, headers=headers)
    if response.status_code >= 400:
        raise UpstreamError(f"上游响应错误: {response.status_code}", status_code=response.status_code)
    try:
        return response.json()
    except ValueError as error:
        raise UpstreamError("上游返回了非 JSON 响应") from error


async def fetch_bytes(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = 2 * 1024 * 1024,
    headers: dict | None = None,
) -> bytes | None:
    """尽力而为地取回一小段二进制（如图标）；任何失败都返回 None。"""
    if not url:
        return None
    target = f"https:{url}" if url.startswith("//") else url
    try:
        async with make_client(timeout=timeout) as client:
            response = await client.get(target, headers=headers)
    except Exception:  # noqa: BLE001 - 图标为非关键资源
        return None
    if response.status_code >= 400:
        return None
    data = response.content
    if not data or len(data) > max_bytes:
        return None
    return data

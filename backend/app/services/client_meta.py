"""从代理请求头提取客户端信息，并按需查询 IP 归属地。

网关由 API 客户端（SDK、curl、CLI）调用，不是浏览器，所以只记录
网络来源（IP + 归属地）和客户端标识，不记录 Referer、设备、浏览器。
"""

from __future__ import annotations

import ipaddress
import re
import time
from typing import Any

import httpx

from app.config import get_settings

_UA_LIMIT = 512
_LOCATION_LIMIT = 128

# 归属地按 IP 进程内缓存：成功缓存 6 小时，失败缓存 10 分钟，避免每个请求都外呼。
_LOCATION_TTL = 6 * 3600
_LOCATION_FAIL_TTL = 600
_LOCATION_CACHE_MAX = 4096
_location_cache: dict[str, tuple[float, str | None]] = {}

# 常见 API 客户端 / 命令行工具的 User-Agent 标记，按顺序匹配
_CLIENT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("curl/", "curl"),
    ("wget/", "wget"),
    ("postmanruntime", "Postman"),
    ("insomnia", "Insomnia"),
    ("openai-python", "OpenAI SDK"),
    ("openai/python", "OpenAI SDK"),
    ("openai/node", "OpenAI SDK"),
    ("openai/", "OpenAI SDK"),
    ("anthropic", "Anthropic SDK"),
    ("langchain", "LangChain"),
    ("python-requests", "python-requests"),
    ("python-httpx", "httpx"),
    ("httpx/", "httpx"),
    ("aiohttp/", "aiohttp"),
    ("go-http-client", "Go HTTP"),
    ("okhttp", "OkHttp"),
    ("java/", "Java HTTP"),
    ("node-fetch", "node-fetch"),
    ("undici", "undici"),
    ("axios/", "axios"),
    ("node", "Node HTTP"),
    ("git/", "git"),
)


def trusted_proxy(peer: str | None) -> bool:
    parsed = _parse_ip(peer or "")
    if parsed is None:
        return False
    for item in (get_settings().trusted_proxy_cidrs or "").split(","):
        network = _parse_network(item.strip())
        if network is not None and parsed in network:
            return True
    return False


def client_ip(
    headers: dict[str, str] | None,
    fallback: str | None = None,
    *,
    peer: str | None = None,
) -> str | None:
    if trusted_proxy(peer):
        lowered = _lower_headers(headers)
        forwarded = lowered.get("x-forwarded-for", "")
        for candidate in forwarded.split(","):
            parsed = _public_or_any(candidate)
            if parsed:
                return parsed
        for name in ("x-real-ip", "cf-connecting-ip", "true-client-ip"):
            parsed = _public_or_any(lowered.get(name, ""))
            if parsed:
                return parsed
    return _public_or_any(peer or fallback or "")


def client_meta(
    headers: dict[str, str] | None,
    fallback_ip: str | None = None,
    *,
    peer: str | None = None,
) -> dict[str, str | None]:
    lowered = _lower_headers(headers)
    user_agent = _clip(lowered.get("user-agent"), _UA_LIMIT)
    return {
        "client_ip": client_ip(headers, fallback_ip, peer=peer),
        "user_agent": user_agent,
        "client_name": parse_client_name(user_agent or ""),
    }


def parse_client_name(user_agent: str) -> str | None:
    lowered = (user_agent or "").lower()
    if not lowered:
        return None
    for needle, label in _CLIENT_PATTERNS:
        if needle in lowered:
            return label
    return None


async def lookup_ip_location(ip: str | None) -> str | None:
    if not ip or not _is_public(ip):
        return None
    template = (get_settings().ip_geo_url or "").strip()
    if not template:
        return None
    now = time.monotonic()
    cached = _location_cache.get(ip)
    if cached is not None and now - cached[0] < (_LOCATION_TTL if cached[1] else _LOCATION_FAIL_TTL):
        return cached[1]
    location: str | None = None
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(template.format(ip=ip))
        response.raise_for_status()
        location = _format_location(response.json())
    except (httpx.HTTPError, ValueError):
        location = None
    if len(_location_cache) >= _LOCATION_CACHE_MAX:
        _location_cache.clear()
    _location_cache[ip] = (now, location)
    return location


def _format_location(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("status") not in (None, "success"):
        return None
    parts = [
        str(payload.get(key) or "").strip()
        for key in ("country", "regionName", "region", "city")
    ]
    text = " ".join(part for part in parts if part)
    return _clip(text, _LOCATION_LIMIT)


def _lower_headers(headers: dict[str, str] | None) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in (headers or {}).items() if value}


def _clip(value: str | None, limit: int) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    return text[:limit]


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    text = _strip_ip(value)
    if not text:
        return None
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def _parse_network(value: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    text = value.strip()
    if not text:
        return None
    if "/" not in text:
        text = f"{text}/32" if ":" not in text else f"{text}/128"
    try:
        return ipaddress.ip_network(text, strict=False)
    except ValueError:
        return None


def _public_or_any(value: str) -> str | None:
    parsed = _parse_ip(value)
    return str(parsed) if parsed is not None else None


def _strip_ip(value: str) -> str:
    text = value.strip().strip('"')
    if text.startswith("[") and "]" in text:
        text = text[1 : text.index("]")]
    text = re.sub(r":\d+$", "", text) if text.count(":") == 1 else text
    return text


def _is_public(value: str) -> bool:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (parsed.is_private or parsed.is_loopback or parsed.is_reserved or parsed.is_link_local)

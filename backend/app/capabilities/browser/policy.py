"""导航 URL 安全策略：协议白名单 + 私网拦截 + 域名黑白名单。"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.capabilities.browser.errors import BrowserError

_ALLOWED_SCHEMES = {"http", "https"}
# 云元数据等特殊地址，即便落在 link-local 之外也一并拒绝
_BLOCKED_IPS = {"169.254.169.254", "100.100.100.200", "fd00:ec2::254"}


def _hosts(value: str | None) -> tuple[str, ...]:
    return tuple(
        item.strip().lower().lstrip(".")
        for item in (value or "").split(",")
        if item.strip()
    )


def _host_matches(host: str, patterns: tuple[str, ...]) -> bool:
    return any(host == item or host.endswith("." + item) for item in patterns)


def _is_blocked_ip(address: str) -> bool:
    if address in _BLOCKED_IPS:
        return True
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _assert_public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        # 解析失败交给实际请求报错，避免 DNS 抖动导致误拒
        return
    for info in infos:
        address = info[4][0]
        if _is_blocked_ip(address):
            raise BrowserError("目标地址不可访问", status_code=400, error_type="blocked_host")


def assert_navigable_url(
    url: str,
    *,
    allowed_hosts: str | None = None,
    blocked_hosts: str | None = None,
) -> str:
    raw = (url or "").strip()
    if not raw:
        raise BrowserError("url 不能为空")
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise BrowserError("只允许 http/https 地址", status_code=400, error_type="blocked_host")
    host = (parsed.hostname or "").lower()
    if not host:
        raise BrowserError("链接缺少主机名", status_code=400, error_type="blocked_host")

    blocked = _hosts(blocked_hosts)
    if blocked and _host_matches(host, blocked):
        raise BrowserError("目标域名已被禁止", status_code=400, error_type="blocked_host")
    allowed = _hosts(allowed_hosts)
    if allowed and not _host_matches(host, allowed):
        raise BrowserError("目标域名不在允许范围", status_code=400, error_type="blocked_host")

    _assert_public_host(host)
    return host

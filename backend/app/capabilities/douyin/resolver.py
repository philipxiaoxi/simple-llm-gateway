from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from .errors import DouyinError

# 只接受抖音自有域；媒体 CDN 域名由提取器给出，单独放行，不在此白名单内。
DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")

_GENERIC_URL = re.compile(r"https?://[^\s\"'<>）)，。、；】]+", re.IGNORECASE)
_SHORT = re.compile(r"https?://v\.douyin\.com/[A-Za-z0-9_-]+/?", re.IGNORECASE)
_VIDEO = re.compile(r"https?://(?:www\.)?douyin\.com/video/(\d+)", re.IGNORECASE)
_NOTE = re.compile(r"https?://(?:www\.)?douyin\.com/note/(\d+)", re.IGNORECASE)
_IES_VIDEO = re.compile(r"https?://(?:www\.)?iesdouyin\.com/share/video/(\d+)", re.IGNORECASE)

_PATTERNS = (_SHORT, _VIDEO, _NOTE, _IES_VIDEO)


@dataclass
class Target:
    url: str


def is_allowed_host(host: str) -> bool:
    host = (host or "").strip().lower().rstrip(".")
    return any(host == item or host.endswith("." + item) for item in DOUYIN_HOSTS)


def _assert_public_ip(host: str) -> None:
    """解析主机并拒绝私有/回环/保留地址。解析失败时放行，交由实际请求失败处理。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise DouyinError("链接指向内网地址，已拒绝", status_code=400, error_type="blocked_host")


def assert_safe_url(url: str, *, allow_hosts: tuple[str, ...] = ()) -> str:
    """校验外部请求 URL：协议、可选主机白名单、公网 IP。返回主机名。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise DouyinError("只支持 http/https 链接", status_code=400, error_type="blocked_host")
    host = (parsed.hostname or "").lower()
    if not host:
        raise DouyinError("链接缺少主机名", status_code=400, error_type="blocked_host")
    if allow_hosts and not any(host == item or host.endswith("." + item) for item in allow_hosts):
        raise DouyinError(f"链接主机不在允许范围: {host}", status_code=400, error_type="blocked_host")
    _assert_public_ip(host)
    return host


def extract_url(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    candidates = _GENERIC_URL.findall(raw)
    if not candidates:
        # 输入本身就是裸链接（没有可识别的 scheme 之外的字符）
        if raw.lower().startswith(("http://", "https://")):
            candidates = [raw]
        else:
            return None
    for pattern in _PATTERNS:
        for candidate in candidates:
            match = pattern.search(candidate)
            if match:
                return match.group(0)
    for candidate in candidates:
        host = (urlparse(candidate).hostname or "").lower()
        if is_allowed_host(host):
            return candidate
    # 找到了 http(s) 链接但主机不在白名单：交给 resolve_target 报 blocked_host，
    # 而不是笼统地说“没有链接”。
    for candidate in candidates:
        if candidate.lower().startswith(("http://", "https://")):
            return candidate
    return None


def resolve_target(raw: str) -> Target:
    url = extract_url(raw)
    if not url:
        raise DouyinError("未找到可识别的抖音链接")
    assert_safe_url(url, allow_hosts=DOUYIN_HOSTS)
    return Target(url=url)

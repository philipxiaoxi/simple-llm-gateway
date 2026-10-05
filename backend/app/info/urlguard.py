"""外部请求的安全校验与渠道标识归一化。

资讯收集有两个外部入口：

1. **渠道标识**（用户输入）：归一化成 Telegram 用户名后交给 TikHub，主机固定。
2. **媒体直链**（上游响应）：Telegram CDN 地址带签名且会过期，属于**不可信输入**，
   因此下载时逐跳校验主机与解析出的 IP。

媒体下载复用与本项目抖音下载相同的防护策略：关闭自动重定向、手动逐跳、
拒绝私有/回环/保留地址。
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

from .errors import InfoError

# Telegram 媒体 CDN：cdn1.telesco.pe / cdn4.telesco.pe 等
MEDIA_HOSTS = ("telesco.pe", "cdn-telegram.org")

_CHANNEL_URL = re.compile(
    # 允许省略 scheme：`t.me/xxx` 是最常见的粘贴形式
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]{3,64})/?",
    re.IGNORECASE,
)
_CHANNEL_BARE = re.compile(r"^@?([A-Za-z0-9_]{3,64})$")


def is_allowed_host(host: str, allow_hosts: tuple[str, ...]) -> bool:
    host = (host or "").strip().lower().rstrip(".")
    return any(host == item or host.endswith("." + item) for item in allow_hosts)


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
            raise InfoError("链接指向内网地址，已拒绝", status_code=400, error_type="blocked_host")


def assert_safe_url(url: str, *, allow_hosts: tuple[str, ...] = ()) -> str:
    """校验外部请求 URL：协议、可选主机白名单、公网 IP。返回主机名。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise InfoError("只支持 http/https 链接", status_code=400, error_type="blocked_host")
    host = (parsed.hostname or "").lower()
    if not host:
        raise InfoError("链接缺少主机名", status_code=400, error_type="blocked_host")
    if allow_hosts and not is_allowed_host(host, allow_hosts):
        raise InfoError(f"链接主机不在允许范围: {host}", status_code=400, error_type="blocked_host")
    _assert_public_ip(host)
    return host


def normalize_channel(raw: str) -> str:
    """把用户粘贴的各种写法归一化成 Telegram 频道用户名（不含 @）。

    接受：`https://t.me/xxx`、`t.me/xxx`、`https://t.me/s/xxx`、`@xxx`、`xxx`，
    也接受这些形态夹在整段文本里的情况（例如从网页复制的一整句话）。
    """
    text = (raw or "").strip()
    if not text:
        raise InfoError("请填写频道地址或用户名", status_code=400, error_type="invalid_channel")

    match = _CHANNEL_URL.search(text)
    if match:
        return match.group(1)

    # 裸用户名：允许带 @ 前缀，也允许前后有少量空白/标点
    stripped = text.strip().strip("，。,.；;、")
    bare = _CHANNEL_BARE.match(stripped)
    if bare:
        return bare.group(1)

    # 整段文本里出现 @name 的情况
    at_match = re.search(r"@([A-Za-z0-9_]{3,64})", text)
    if at_match:
        return at_match.group(1)

    raise InfoError(
        "无法识别的频道，请填 @name 或 https://t.me/name",
        status_code=400,
        error_type="invalid_channel",
    )


def channel_permalink(identifier: str, post_id: str | int) -> str:
    return f"https://t.me/{identifier}/{post_id}"

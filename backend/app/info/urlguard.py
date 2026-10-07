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
TELEGRAM_MEDIA_HOSTS = ("telesco.pe", "cdn-telegram.org")
# 微信公众号图片：mmbiz.qpic.cn 为正文/封面图，qlogo 为头像
WECHAT_MEDIA_HOSTS = ("mmbiz.qpic.cn", "mmbiz.qlogo.cn", "wx.qlogo.cn")
MEDIA_HOSTS = (*TELEGRAM_MEDIA_HOSTS, *WECHAT_MEDIA_HOSTS)

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


# 微信公众号文章链接：https://mp.weixin.qq.com/s/xxx 或带 __biz 的长链
_WECHAT_URL = re.compile(r"https?://mp\.weixin\.qq\.com/s([/?][^\s]*)?", re.IGNORECASE)
# 公众号 username：gh_…、gh_…@app、以及自定义微信号（与上游 OpenAPI pattern 一致）
_WECHAT_USERNAME = re.compile(r"^[0-9A-Za-z][-_0-9A-Za-z]{1,63}(@[0-9A-Za-z]{1,16})?$")


def wechat_article_url(value: str) -> str | None:
    """从输入中提取微信公众号文章链接；没有则返回 None。"""
    match = _WECHAT_URL.search(value or "")
    return match.group(0) if match else None


def normalize_wechat(raw: str) -> str:
    """归一化微信公众号输入：文章链接保留原链接，用户名去掉 @ 前缀。

    接受：`gh_xxx`、`gh_xxx@app`、自定义微信号（如 `nikejdi`）、
    `https://mp.weixin.qq.com/s/…` 文章链接（含夹在整段文本里的情况）。
    文章链接不做解析，交给预览/创建时通过 `fetch_article_detail` 反查 username。
    """
    text = (raw or "").strip()
    if not text:
        raise InfoError("请填写公众号名称或文章链接", status_code=400, error_type="invalid_channel")

    url = wechat_article_url(text)
    if url:
        return url

    stripped = text.strip().strip("，。,.；;、").lstrip("@")
    if _WECHAT_USERNAME.match(stripped):
        return stripped

    raise InfoError(
        "无法识别的公众号，请填 gh_ 开头的 username、微信号或 mp.weixin.qq.com 文章链接",
        status_code=400,
        error_type="invalid_channel",
    )


def normalize_feed_url(raw: str) -> str:
    """归一化 RSS/Atom 订阅地址：必须是 http(s) 且指向公网。

    RSS 是用户自定义的任意主机，服务端会主动请求，因此在这里做一次 SSRF 校验，
    拒绝私网/回环/保留地址。
    """
    text = (raw or "").strip()
    if not text:
        raise InfoError("请填写 RSS 地址", status_code=400, error_type="invalid_channel")
    if not text.lower().startswith(("http://", "https://")):
        raise InfoError(
            "RSS 地址需以 http:// 或 https:// 开头", status_code=400, error_type="invalid_channel"
        )
    assert_safe_url(text)
    return text

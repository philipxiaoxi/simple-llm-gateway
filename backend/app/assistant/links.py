"""把工具结果里的内网绝对地址改写成浏览器当前访问的站点地址。

能力返回的资源链接通常按 `app_base_url` 拼绝对地址，默认是 `http://127.0.0.1:8000`。
预览/反向代理场景下浏览器拿到的是公网域名，这个地址不可达；助手按请求的 Origin
动态改写，图片与预览链接才能在聊天面板里直接打开。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from app.config import get_settings


def public_origin_from_request(request) -> str:
    """从 Origin / Referer 取浏览器实际访问的源；没有就返回空串。"""

    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if not value:
            continue
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def rewrite_public_links(value: Any, origin: str) -> Any:
    """递归把以 app_base_url 开头的字符串换成公开 origin。"""

    base = (get_settings().app_base_url or "").rstrip("/")
    public = str(origin or "").rstrip("/")
    if not base or not public or base == public:
        return value

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            if item == base:
                return public
            if item.startswith(f"{base}/"):
                return f"{public}{item[len(base):]}"
            return item
        if isinstance(item, list):
            return [walk(entry) for entry in item]
        if isinstance(item, dict):
            return {key: walk(entry) for key, entry in item.items()}
        return item

    return walk(value)

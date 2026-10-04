"""TikHub 凭据的共享读取。

抖音下载与资讯收集用的是同一个 TikHub 账号（一个 token 跨平台通用），所以凭据只保留
一份：优先读数据库里加密存储的那份（由抖音设置页写入），环境变量兜底。资讯页只展示
状态并提示「与抖音下载共用」，不重复配置。
"""

from __future__ import annotations

from typing import Any

from app.capabilities.douyin import provider_config
from app.config import get_settings

DEFAULT_BASE_URL = "https://api.tikhub.io"


def get_tikhub_credentials(db: Any) -> tuple[str, str]:
    """返回 (base_url, api_key)；读不到时给默认 Base URL 与空 Key。"""
    base_url = ""
    api_key = ""
    try:
        base_url, api_key = provider_config.get_config(db)
    except Exception:
        # 表缺失或解密失败都不应让资讯功能整体不可用，退到环境变量
        base_url, api_key = "", ""
    settings = get_settings()
    if not base_url:
        base_url = (
            settings.info_tikhub_base_url or settings.douyin_tikhub_base_url or ""
        ).strip()
    if not api_key:
        api_key = (settings.info_tikhub_api_key or "").strip()
    return (base_url or DEFAULT_BASE_URL), api_key


def tikhub_status(db: Any) -> dict[str, Any]:
    base_url, api_key = get_tikhub_credentials(db)
    source = ""
    try:
        source = str(provider_config.status(db).get("source") or "")
    except Exception:
        source = ""
    if not source and api_key:
        source = "env"
    return {
        "configured": bool(base_url and api_key),
        "has_key": bool(api_key),
        "source": source,
        "base_url": base_url,
        "shared_with_douyin": True,
    }

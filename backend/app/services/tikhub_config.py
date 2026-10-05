"""TikHub 凭据的共享读写。

抖音下载与资讯收集用的是同一个 TikHub 账号（一个 token 跨平台通用），凭据只保留一份：
优先读数据库中加密存储的 `tikhub_settings` 单例记录，读不到再回退环境变量
（`TIKHUB_*` → `DOUYIN_*` → `INFO_*`）。清除时保留单例空记录，避免旧 `douyin_settings`
数据在重启时被迁移逻辑重新导入。
"""

from __future__ import annotations

from typing import Any

from app.clock import utcnow
from app.config import get_settings
from app.crypto import decrypt_secret, encrypt_secret
from app.models import TikHubSettings

DEFAULT_BASE_URL = "https://api.tikhub.io"


class TikHubConfigError(Exception):
    """凭据校验失败。"""


def _row(db: Any, *, create: bool = False) -> TikHubSettings | None:
    row = db.get(TikHubSettings, 1)
    if row is None and create:
        row = TikHubSettings(id=1)
        db.add(row)
        db.flush()
    return row


def _env_credentials(settings: Any) -> tuple[str, str]:
    base_url = (
        settings.tikhub_base_url
        or settings.douyin_tikhub_base_url
        or settings.info_tikhub_base_url
        or ""
    ).strip()
    api_key = (
        settings.tikhub_api_key
        or settings.douyin_tikhub_api_key
        or settings.info_tikhub_api_key
        or ""
    ).strip()
    return base_url, api_key


def get_tikhub_credentials(db: Any) -> tuple[str, str]:
    """返回 (base_url, api_key)；读不到时给默认 Base URL 与空 Key。"""
    settings = get_settings()
    row = _row(db)
    base_url = ""
    api_key = ""
    if row is not None:
        base_url = (row.base_url or "").strip()
        if row.api_key_encrypted:
            try:
                api_key = decrypt_secret(row.api_key_encrypted, settings.app_secret_key).strip()
            except ValueError:
                # 解密失败不应让功能整体不可用，退到环境变量
                api_key = ""
    env_base_url, env_api_key = _env_credentials(settings)
    if not base_url:
        base_url = env_base_url
    if not api_key:
        api_key = env_api_key
    return (base_url or DEFAULT_BASE_URL), api_key


def tikhub_status(db: Any) -> dict[str, Any]:
    settings = get_settings()
    row = _row(db)
    base_url, api_key = get_tikhub_credentials(db)
    page_configured = bool(row and (row.base_url or row.api_key_encrypted))
    if page_configured:
        source = "page"
    elif _env_credentials(settings)[1]:
        source = "env"
    else:
        source = ""
    return {
        "configured": bool(base_url and api_key),
        "has_key": bool(api_key),
        "source": source,
        "base_url": base_url,
        "shared_with_douyin": True,
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
    }


def set_tikhub_config(
    db: Any, *, base_url: str | None = None, api_key: str | None = None
) -> None:
    row = _row(db, create=True)
    assert row is not None
    settings = get_settings()
    if base_url is not None:
        cleaned = str(base_url).strip()
        if cleaned and not cleaned.startswith(("http://", "https://")):
            raise TikHubConfigError("base_url 必须以 http:// 或 https:// 开头")
        row.base_url = cleaned[:256]
    if api_key is not None:
        cleaned = str(api_key).strip()
        if not cleaned:
            raise TikHubConfigError("api_key 不能为空")
        row.api_key_encrypted = encrypt_secret(cleaned, settings.app_secret_key)
    row.updated_at = utcnow()
    db.flush()


def clear_tikhub_config(db: Any) -> None:
    # 保留单例记录（只清空字段），让启动迁移的「无记录」判定保持为假
    row = _row(db, create=True)
    assert row is not None
    row.base_url = ""
    row.api_key_encrypted = None
    row.updated_at = None
    db.flush()

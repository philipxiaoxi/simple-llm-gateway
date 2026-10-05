"""抖音下载的 TikHub 凭据读写。

凭据已抽为全局共享配置（`app/services/tikhub_config.py`），本模块仅保留抖音下载侧的
旧调用入口，把共享服务的校验异常翻译成 `DouyinError`。
"""

from __future__ import annotations

from typing import Any

from app.services import tikhub_config
from app.services.tikhub_config import TikHubConfigError

from .errors import DouyinError


def get_config(db: Any) -> tuple[str, str]:
    """返回 (base_url, api_key)。"""
    return tikhub_config.get_tikhub_credentials(db)


def status(db: Any) -> dict[str, Any]:
    return tikhub_config.tikhub_status(db)


def set_config(db: Any, *, base_url: str | None = None, api_key: str | None = None) -> None:
    try:
        tikhub_config.set_tikhub_config(db, base_url=base_url, api_key=api_key)
    except TikHubConfigError as error:
        raise DouyinError(str(error)) from error


def clear_config(db: Any) -> None:
    tikhub_config.clear_tikhub_config(db)

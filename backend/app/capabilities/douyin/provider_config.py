from __future__ import annotations

from typing import Any

from app.clock import utcnow
from app.config import get_settings
from app.crypto import decrypt_secret, encrypt_secret
from app.models import DouyinSettings

from .errors import DouyinError


def _row(db, *, create: bool = False) -> DouyinSettings | None:
    row = db.get(DouyinSettings, 1)
    if row is None and create:
        row = DouyinSettings(id=1)
        db.add(row)
        db.flush()
    return row


def get_config(db) -> tuple[str, str]:
    """返回 (base_url, api_key)。页面配置优先，环境变量兜底。"""
    settings = get_settings()
    row = _row(db)
    base_url = ""
    api_key = ""
    if row is not None:
        base_url = (row.tikhub_base_url or "").strip()
        if row.tikhub_api_key_encrypted:
            try:
                api_key = decrypt_secret(row.tikhub_api_key_encrypted, settings.app_secret_key).strip()
            except ValueError:
                api_key = ""
    if not base_url:
        base_url = (settings.douyin_tikhub_base_url or "").strip()
    if not api_key:
        api_key = (settings.douyin_tikhub_api_key or "").strip()
    return base_url, api_key


def status(db) -> dict[str, Any]:
    settings = get_settings()
    row = _row(db)
    base_url, api_key = get_config(db)
    page_configured = bool(row and (row.tikhub_base_url or row.tikhub_api_key_encrypted))
    if page_configured:
        source = "page"
    elif settings.douyin_tikhub_api_key:
        source = "env"
    else:
        source = ""
    return {
        "base_url": base_url,
        "configured": bool(base_url and api_key),
        "has_key": bool(api_key),
        "source": source,
        "updated_at": row.tikhub_updated_at.isoformat() if row and row.tikhub_updated_at else None,
    }


def set_config(db, *, base_url: str | None = None, api_key: str | None = None) -> None:
    row = _row(db, create=True)
    assert row is not None
    if base_url is not None:
        cleaned = str(base_url).strip()
        if cleaned and not cleaned.startswith(("http://", "https://")):
            raise DouyinError("base_url 必须以 http:// 或 https:// 开头")
        row.tikhub_base_url = cleaned[:256]
    if api_key is not None:
        cleaned = str(api_key).strip()
        if not cleaned:
            raise DouyinError("api_key 不能为空")
        row.tikhub_api_key_encrypted = encrypt_secret(cleaned, get_settings().app_secret_key)
    row.tikhub_updated_at = utcnow()
    row.updated_at = utcnow()
    db.flush()


def clear_config(db) -> None:
    row = _row(db)
    if row is None:
        return
    row.tikhub_base_url = ""
    row.tikhub_api_key_encrypted = None
    row.tikhub_updated_at = None
    row.updated_at = utcnow()
    db.flush()

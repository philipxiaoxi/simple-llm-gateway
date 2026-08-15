from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import (
    MIN_EXPORT_PASSWORD_LENGTH,
    decrypt_secret,
    decrypt_with_password,
    encrypt_secret,
    encrypt_with_password,
)
from app.models import UpstreamAccount
from app.providers import get_provider


def unique_account_name(taken: set[str], name: str) -> str:
    trimmed = name.strip() or "未命名账号"
    if trimmed not in taken:
        return trimmed
    index = 1
    while f"{trimmed}（{index}）" in taken:
        index += 1
    return f"{trimmed}（{index}）"


def export_accounts(db: Session, password: str) -> dict[str, object]:
    if len(password) < MIN_EXPORT_PASSWORD_LENGTH:
        raise ValueError(f"密码至少 {MIN_EXPORT_PASSWORD_LENGTH} 位")
    settings = get_settings()
    rows = db.scalars(select(UpstreamAccount).order_by(UpstreamAccount.id)).all()
    accounts: list[dict[str, object]] = []
    for account in rows:
        api_key = None
        if account.auth_type == "api_key" and account.api_key_encrypted:
            api_key = decrypt_secret(account.api_key_encrypted, settings.app_secret_key)
        accounts.append(
            {
                "name": account.name,
                "provider": account.provider,
                "base_url": account.base_url,
                "status": account.status,
                "api_key": api_key,
            }
        )
    payload = json.dumps({"accounts": accounts}, ensure_ascii=False)
    return encrypt_with_password(payload, password)


def import_accounts(db: Session, password: str, envelope: dict[str, object]) -> dict[str, int]:
    if len(password) < MIN_EXPORT_PASSWORD_LENGTH:
        raise ValueError(f"密码至少 {MIN_EXPORT_PASSWORD_LENGTH} 位")
    raw = decrypt_with_password(envelope, password)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("解密内容不是合法 JSON") from error
    entries = parsed.get("accounts") if isinstance(parsed, dict) else None
    if not isinstance(entries, list):
        raise ValueError("文件里没有账号列表")
    settings = get_settings()
    taken = set(db.scalars(select(UpstreamAccount.name)).all())
    created = 0
    skipped = 0
    for entry in entries:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        provider_id = str(entry.get("provider") or "").strip()
        try:
            provider = get_provider(provider_id)
        except ValueError:
            skipped += 1
            continue
        name = unique_account_name(taken, str(entry.get("name") or ""))
        taken.add(name)
        api_key = entry.get("api_key")
        encrypted = None
        if provider.auth_type == "api_key" and isinstance(api_key, str) and api_key.strip():
            encrypted = encrypt_secret(api_key.strip(), settings.app_secret_key)
        status = str(entry.get("status") or "active")
        if status not in {"active", "disabled"}:
            status = "active"
        account = UpstreamAccount(
            name=name,
            provider=provider.id,
            auth_type=provider.auth_type,
            base_url=(str(entry.get("base_url") or "").strip() or provider.default_base_url),
            api_key_encrypted=encrypted,
            status=status,
            updated_at=datetime.utcnow(),
        )
        db.add(account)
        db.flush()
        initial_quota = provider.initial_quota()
        if initial_quota is not None:
            provider.store_quota(account, initial_quota)
        created += 1
    return {"created": created, "skipped": skipped}

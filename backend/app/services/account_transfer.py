from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
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
from app.schemas import normalize_website_url
from app.services.header_spoof import default_header_spoof, normalize_header_spoof
from app.services.key_models import ensure_account_prefix, normalize_model_prefix


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
                "website_url": account.website_url,
                "status": account.status,
                "risk_level": account.risk_level,
                "model_prefix": account.model_prefix,
                "header_spoof": account.header_spoof,
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
        risk_level = str(entry.get("risk_level") or "low")
        if risk_level not in {"low", "medium", "high"}:
            risk_level = "low"
        try:
            website_url = normalize_website_url(entry.get("website_url") if isinstance(entry.get("website_url"), str) else None)
        except ValueError:
            website_url = None
        try:
            model_prefix = normalize_model_prefix(
                str(entry.get("model_prefix")) if entry.get("model_prefix") is not None else None
            )
        except ValueError:
            model_prefix = None
        try:
            header_spoof = (
                normalize_header_spoof(str(entry.get("header_spoof")))
                if entry.get("header_spoof") is not None
                else default_header_spoof(provider.id)
            )
        except ValueError:
            header_spoof = default_header_spoof(provider.id)
        account = UpstreamAccount(
            name=name,
            provider=provider.id,
            auth_type=provider.auth_type,
            base_url=(str(entry.get("base_url") or "").strip() or provider.default_base_url),
            website_url=website_url or None,
            api_key_encrypted=encrypted,
            status=status,
            risk_level=risk_level,
            model_prefix=model_prefix,
            header_spoof=header_spoof,
            updated_at=utcnow(),
        )
        db.add(account)
        db.flush()
        if not account.model_prefix:
            ensure_account_prefix(account)
        db.flush()
        initial_quota = provider.initial_quota()
        if initial_quota is not None:
            provider.store_quota(account, initial_quota)
        created += 1
    return {"created": created, "skipped": skipped}

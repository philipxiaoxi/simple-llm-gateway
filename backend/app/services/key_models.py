from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import ApiKey, ApiKeyAccount, UpstreamAccount
from app.services.model_caps import ModelRecord, parse_model_records, serialize_record

PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
PREFIX_ERROR = "模型前缀仅允许字母、数字、下划线和短横线，最长 32 位，且不能以符号开头"


@dataclass(frozen=True)
class CatalogEntry:
    public_id: str
    raw_id: str
    account: UpstreamAccount
    record: ModelRecord | None = None


def slug_model_prefix(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (name or "").strip())
    slug = re.sub(r"[-_]{2,}", "-", slug).strip("-_")
    slug = slug[:32].strip("-_")
    if not slug or not PREFIX_PATTERN.match(slug):
        return ""
    return slug


def default_model_prefix(name: str, account_id: int) -> str:
    slug = slug_model_prefix(name)
    if slug:
        return slug
    return f"acc-{account_id}"


def normalize_model_prefix(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = str(value).strip()
    if not trimmed:
        return None
    if "/" in trimmed or not PREFIX_PATTERN.match(trimmed):
        raise ValueError(PREFIX_ERROR)
    return trimmed


def ensure_account_prefix(account: UpstreamAccount) -> str:
    current = (account.model_prefix or "").strip()
    if current:
        account.model_prefix = current
        return current
    prefix = default_model_prefix(account.name, int(account.id))
    account.model_prefix = prefix
    return prefix


def bound_accounts(api_key: ApiKey) -> list[UpstreamAccount]:
    links = list(api_key.account_links or [])
    links.sort(key=lambda item: item.sort_order)
    accounts = [link.account for link in links if link.account is not None]
    if accounts:
        return accounts
    if api_key.account is not None:
        return [api_key.account]
    return []


def active_bound_accounts(api_key: ApiKey) -> list[UpstreamAccount]:
    return [account for account in bound_accounts(api_key) if is_account_available(account)]


def is_account_available(account: UpstreamAccount) -> bool:
    if account.status != "active":
        return False
    if getattr(account, "source", None) == "agent" and account.agent_route_id:
        from app.services.local_agent_relay import local_agent_relay

        return local_agent_relay.is_agent_online_for_route(account.agent_route_id)
    return True


def account_prefix(account: UpstreamAccount) -> str:
    current = (account.model_prefix or "").strip()
    if current:
        return current
    return default_model_prefix(account.name, int(account.id))


def build_model_catalog(api_key: ApiKey) -> list[CatalogEntry]:
    accounts = active_bound_accounts(api_key)
    occupied: set[str] = set()
    entries: list[CatalogEntry] = []
    for account in accounts:
        records = parse_model_records(account.models_json)
        if not records:
            continue
        prefix = account_prefix(account)
        for record in records:
            raw_id = record.id
            if raw_id not in occupied:
                public_id = raw_id
            else:
                public_id = f"{prefix}/{raw_id}"
                if public_id in occupied:
                    public_id = f"{prefix}-{account.id}/{raw_id}"
            occupied.add(public_id)
            entries.append(CatalogEntry(public_id=public_id, raw_id=raw_id, account=account, record=record))
    return entries


def resolve_model(
    catalog: list[CatalogEntry],
    public_id: str,
    *,
    single_account: UpstreamAccount | None = None,
) -> CatalogEntry | None:
    model = (public_id or "").strip()
    if not model:
        return None
    for entry in catalog:
        if entry.public_id == model:
            return entry
    if single_account is not None and not catalog:
        return CatalogEntry(public_id=model, raw_id=model, account=single_account)
    return None


def public_model_ids(api_key: ApiKey) -> list[str]:
    return [entry.public_id for entry in build_model_catalog(api_key)]


def public_model_records(api_key: ApiKey) -> list[dict]:
    items: list[dict] = []
    for entry in build_model_catalog(api_key):
        if entry.record is None:
            items.append({"id": entry.public_id})
            continue
        payload = serialize_record(entry.record)
        payload["id"] = entry.public_id
        items.append(payload)
    return items


def replace_key_accounts(db: Session, item: ApiKey, account_ids: list[int]) -> list[UpstreamAccount]:
    unique_ids: list[int] = []
    seen: set[int] = set()
    for account_id in account_ids:
        if account_id in seen:
            continue
        seen.add(account_id)
        unique_ids.append(account_id)
    if not unique_ids:
        raise ValueError("请至少绑定一个上游账号")
    accounts: list[UpstreamAccount] = []
    for account_id in unique_ids:
        account = db.get(UpstreamAccount, account_id)
        if account is None:
            raise ValueError("上游账号不存在")
        accounts.append(account)
    item.account_links.clear()
    db.flush()
    for index, account in enumerate(accounts):
        item.account_links.append(ApiKeyAccount(account_id=account.id, sort_order=index, account=account))
    item.account_id = accounts[0].id
    item.account = accounts[0]
    return accounts

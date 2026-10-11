from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BenchmarkGroup, BenchmarkGroupItem, UpstreamAccount
from app.services.model_caps import parse_model_records


def normalize_name(name: str) -> str:
    return name.strip()


def _member(
    item: BenchmarkGroupItem,
    account: UpstreamAccount | None,
    *,
    present: bool,
    enabled: bool,
    reason: str,
) -> dict[str, Any]:
    available = account is not None and account.status == "active" and present and enabled
    return {
        "account_id": item.account_id,
        "model": item.model,
        "account_name": account.name if account is not None else "",
        "provider": account.provider if account is not None else "",
        "available": available,
        "enabled": enabled,
        "reason": reason,
    }


def resolve_members(db: Session, items: list[BenchmarkGroupItem]) -> list[dict[str, Any]]:
    """实时解析分组成员的可用性，不落库。

    成员可用 = 账号存在且 active，且模型在账号模型清单中且未关闭。模型/账号后续变化
    无需改动分组即可自动反映。
    """
    account_ids = {item.account_id for item in items}
    accounts: dict[int, UpstreamAccount] = {}
    if account_ids:
        accounts = {
            account.id: account
            for account in db.scalars(
                select(UpstreamAccount).where(UpstreamAccount.id.in_(account_ids))
            ).all()
        }
    model_enabled: dict[int, dict[str, bool]] = {}
    resolved: list[dict[str, Any]] = []
    for item in items:
        account = accounts.get(item.account_id)
        if account is None:
            resolved.append(_member(item, None, present=False, enabled=False, reason="账号不存在"))
            continue
        if item.account_id not in model_enabled:
            model_enabled[item.account_id] = {
                record.id: record.enabled for record in parse_model_records(account.models_json)
            }
        catalog = model_enabled[item.account_id]
        present = item.model in catalog
        enabled = bool(catalog.get(item.model))
        if account.status != "active":
            reason = "账号已停用"
        elif not present:
            reason = "模型不存在"
        elif not enabled:
            reason = "模型已关闭"
        else:
            reason = ""
        resolved.append(_member(item, account, present=present, enabled=enabled, reason=reason))
    return resolved


def group_to_dict(db: Session, group: BenchmarkGroup) -> dict[str, Any]:
    members = resolve_members(db, list(group.items))
    return {
        "id": group.id,
        "name": group.name,
        "created_at": group.created_at.isoformat(),
        "updated_at": group.updated_at.isoformat(),
        "member_count": len(members),
        "available_count": sum(1 for member in members if member["available"]),
        "members": members,
    }


def sync_members(db: Session, group: BenchmarkGroup) -> int:
    """移除已下架的成员：账号不存在、或模型已不在账号模型清单中。

    仅在其账号或模型彻底消失时清理；模型仍在清单中但被关闭（enabled=False）的成员保留，
    因为它们仍可被显式测速。返回移除的成员数，由调用方在需要时提交事务。
    """
    items = list(group.items)
    if not items:
        return 0
    account_ids = {item.account_id for item in items}
    accounts = {
        account.id: account
        for account in db.scalars(select(UpstreamAccount).where(UpstreamAccount.id.in_(account_ids))).all()
    }
    catalogs: dict[int, dict[str, bool]] = {}
    removed = 0
    for item in items:
        account = accounts.get(item.account_id)
        if account is None:
            group.items.remove(item)
            removed += 1
            continue
        if item.account_id not in catalogs:
            catalogs[item.account_id] = {
                record.id: record.enabled for record in parse_model_records(account.models_json)
            }
        if item.model not in catalogs[item.account_id]:
            group.items.remove(item)
            removed += 1
    return removed

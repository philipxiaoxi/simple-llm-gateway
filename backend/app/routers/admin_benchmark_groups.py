from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.clock import utcnow
from app.db import get_db
from app.deps import get_current_admin
from app.models import Admin, BenchmarkGroup, BenchmarkGroupItem, UpstreamAccount
from app.services.benchmark_groups import group_to_dict, normalize_name, sync_members

router = APIRouter(
    prefix="/api/admin/benchmark/groups",
    tags=["admin-benchmark-groups"],
    dependencies=[Depends(get_current_admin)],
)


class GroupTargetInput(BaseModel):
    account_id: int = Field(ge=1)
    model: str = Field(min_length=1, max_length=128)


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    targets: list[GroupTargetInput] = Field(max_length=500)


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    targets: list[GroupTargetInput] | None = Field(default=None, max_length=500)


def _get_group(db: Session, group_id: int) -> BenchmarkGroup:
    group = db.scalar(
        select(BenchmarkGroup)
        .options(selectinload(BenchmarkGroup.items))
        .where(BenchmarkGroup.id == group_id)
    )
    if group is None:
        raise HTTPException(status_code=404, detail="分组不存在")
    return group


def _ensure_name_available(db: Session, name: str, exclude_id: int | None = None) -> str:
    normalized = normalize_name(name)
    if not normalized:
        raise HTTPException(status_code=400, detail="分组名称不能为空")
    statement = select(BenchmarkGroup.id).where(func.lower(BenchmarkGroup.name) == normalized.lower())
    if exclude_id is not None:
        statement = statement.where(BenchmarkGroup.id != exclude_id)
    if db.scalar(statement) is not None:
        raise HTTPException(status_code=409, detail="分组名称已存在")
    return normalized


def _dedupe(targets: list[GroupTargetInput]) -> list[GroupTargetInput]:
    seen: set[tuple[int, str]] = set()
    unique: list[GroupTargetInput] = []
    for target in targets:
        key = (target.account_id, target.model)
        if key in seen:
            continue
        seen.add(key)
        unique.append(target)
    return unique


def _validate_targets(db: Session, targets: list[GroupTargetInput]) -> None:
    account_ids = {target.account_id for target in targets}
    existing = set(db.scalars(select(UpstreamAccount.id).where(UpstreamAccount.id.in_(account_ids))).all())
    missing = account_ids - existing
    if missing:
        raise HTTPException(status_code=400, detail=f"账号不存在：{sorted(missing)}")


def _build_items(targets: list[GroupTargetInput]) -> list[BenchmarkGroupItem]:
    return [BenchmarkGroupItem(account_id=target.account_id, model=target.model) for target in targets]


@router.get("")
def list_groups(db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)) -> dict[str, Any]:
    groups = db.scalars(
        select(BenchmarkGroup)
        .options(selectinload(BenchmarkGroup.items))
        .order_by(BenchmarkGroup.updated_at.desc(), BenchmarkGroup.id.desc())
    ).all()
    # 账号删除或模型下架后，成员在读取时同步清理，避免分组残留失效目标
    if sum(sync_members(db, group) for group in groups):
        db.commit()
    return {"items": [group_to_dict(db, group) for group in groups]}


@router.post("")
def create_group(
    payload: GroupCreate,
    db: Session = Depends(get_db),
    _: Admin = Depends(get_current_admin),
) -> dict[str, Any]:
    name = _ensure_name_available(db, payload.name)
    targets = _dedupe(payload.targets)
    if targets:
        _validate_targets(db, targets)
    now = utcnow()
    group = BenchmarkGroup(name=name, created_at=now, updated_at=now)
    group.items = _build_items(targets)
    db.add(group)
    db.commit()
    return group_to_dict(db, _get_group(db, group.id))


@router.patch("/{group_id}")
def update_group(
    group_id: int,
    payload: GroupUpdate,
    db: Session = Depends(get_db),
    _: Admin = Depends(get_current_admin),
) -> dict[str, Any]:
    group = _get_group(db, group_id)
    changed = False
    if payload.name is not None:
        group.name = _ensure_name_available(db, payload.name, exclude_id=group_id)
        changed = True
    if payload.targets is not None:
        targets = _dedupe(payload.targets)
        if targets:
            _validate_targets(db, targets)
        group.items = _build_items(targets)
        changed = True
    if changed:
        group.updated_at = utcnow()
    db.commit()
    return group_to_dict(db, _get_group(db, group_id))


@router.delete("/{group_id}")
def delete_group(
    group_id: int,
    db: Session = Depends(get_db),
    _: Admin = Depends(get_current_admin),
) -> dict[str, Any]:
    group = _get_group(db, group_id)
    db.delete(group)
    db.commit()
    return {"removed": True}

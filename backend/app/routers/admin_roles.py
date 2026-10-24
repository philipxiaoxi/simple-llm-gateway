"""角色管理接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.db import get_db
from app.deps import get_current_admin
from app.models import Admin, Role
from app.permissions import ADMIN_ROLE_NAME, WILDCARD, catalog_payload, validate_codes
from app.schemas import (
    PermissionCatalogResourceOut,
    RoleCreate,
    RoleOut,
    RoleUpdate,
)
from app.services.client_meta import client_meta
from app.services.permission_guard import has_permission, permissions_for_admin, parse_permissions
from app.services.security_audit import enqueue_login_event

router = APIRouter(prefix="/api/admin", tags=["admin-roles"])


def _meta(request: Request) -> dict[str, str | None]:
    return client_meta(dict(request.headers), peer=request.client.host if request.client else None)


def _audit(request: Request, event_type: str, actor: str, path: str, status: int) -> None:
    enqueue_login_event(
        event_type,
        actor,
        _meta(request),
        status=status,
        path=path,
        method=request.method,
    )


def _admin_count(db: Session, role_id: int) -> int:
    return int(db.scalar(select(func.count()).select_from(Admin).where(Admin.role_id == role_id)) or 0)


def _role_out(db: Session, role: Role) -> RoleOut:
    return RoleOut(
        id=role.id,
        name=role.name,
        builtin=bool(role.builtin),
        permissions=sorted(parse_permissions(role.permissions_json)) or ([WILDCARD] if role.builtin else []),
        admin_count=_admin_count(db, role.id),
        created_at=role.created_at,
        updated_at=role.updated_at or role.created_at,
    )


def _ensure_codes_assignable(db: Session, admin: Admin, codes: list[str]) -> None:
    """非通配持有者只能授予自己已有的权限（防提权）。"""
    actor = permissions_for_admin(db, admin)
    if WILDCARD in actor:
        return
    over = sorted(code for code in codes if not has_permission(actor, code))
    if over:
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "type": "permission_denied",
                    "message": f"不能授予自己不具备的权限：{'、'.join(over)}",
                    "permissions": over,
                }
            },
        )


def _load_role(db: Session, role_id: int) -> Role:
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    return role


@router.get("/roles", response_model=list[RoleOut])
def list_roles(db: Session = Depends(get_db)) -> list[RoleOut]:
    roles = db.scalars(select(Role).order_by(Role.builtin.desc(), Role.id.asc())).all()
    return [_role_out(db, role) for role in roles]


@router.post("/roles", response_model=RoleOut, status_code=201)
def create_role(
    payload: RoleCreate,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> RoleOut:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="角色名不能为空")
    unknown = validate_codes(payload.permissions)
    if unknown:
        raise HTTPException(status_code=400, detail=f"未知权限码：{'、'.join(unknown)}")
    _ensure_codes_assignable(db, admin, payload.permissions)
    if db.scalar(select(Role).where(func.lower(Role.name) == name.lower())) is not None:
        raise HTTPException(status_code=409, detail="角色名已存在")
    role = Role(name=name, builtin=False, permissions_json=_dump(payload.permissions))
    db.add(role)
    db.commit()
    db.refresh(role)
    _audit(request, "role_created", admin.username, f"/api/admin/roles/{role.id}", 201)
    return _role_out(db, role)


@router.patch("/roles/{role_id}", response_model=RoleOut)
def update_role(
    role_id: int,
    payload: RoleUpdate,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> RoleOut:
    role = _load_role(db, role_id)
    if role.builtin:
        raise HTTPException(
            status_code=403,
            detail={"error": {"type": "permission_denied", "message": f"内置角色「{ADMIN_ROLE_NAME}」不可修改"}},
        )
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="角色名不能为空")
        dup = db.scalar(
            select(Role).where(func.lower(Role.name) == name.lower(), Role.id != role.id)
        )
        if dup is not None:
            raise HTTPException(status_code=409, detail="角色名已存在")
        role.name = name
    if payload.permissions is not None:
        unknown = validate_codes(payload.permissions)
        if unknown:
            raise HTTPException(status_code=400, detail=f"未知权限码：{'、'.join(unknown)}")
        _ensure_codes_assignable(db, admin, payload.permissions)
        role.permissions_json = _dump(payload.permissions)
    role.updated_at = utcnow()
    db.commit()
    db.refresh(role)
    _audit(request, "role_updated", admin.username, f"/api/admin/roles/{role.id}", 200)
    return _role_out(db, role)


@router.delete("/roles/{role_id}", status_code=204)
def delete_role(
    role_id: int,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    role = _load_role(db, role_id)
    if role.builtin:
        raise HTTPException(
            status_code=403,
            detail={"error": {"type": "permission_denied", "message": f"内置角色「{ADMIN_ROLE_NAME}」不可删除"}},
        )
    count = _admin_count(db, role.id)
    if count:
        raise HTTPException(status_code=409, detail=f"仍有 {count} 个账号使用该角色，请先调整后再删除")
    db.delete(role)
    db.commit()
    _audit(request, "role_deleted", admin.username, f"/api/admin/roles/{role_id}", 204)


@router.get("/permission-catalog", response_model=list[PermissionCatalogResourceOut])
def permission_catalog() -> list[PermissionCatalogResourceOut]:
    return [PermissionCatalogResourceOut(**item) for item in catalog_payload()]


def _dump(codes: list[str]) -> str:
    import json

    return json.dumps(sorted(set(codes)), ensure_ascii=False)

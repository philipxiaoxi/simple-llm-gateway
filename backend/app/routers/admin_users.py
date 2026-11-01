"""用户（管理员账号）管理接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.config import INSECURE_ADMIN_PASSWORDS, MIN_ADMIN_PASSWORD_LENGTH
from app.crypto import hash_password
from app.db import get_db
from app.deps import get_current_admin
from app.models import Admin, Role
from app.nav_tabs import resolve_for_admin, save_default
from app.permissions import WILDCARD
from app.schemas import (
    AdminPasswordReset,
    AdminUserCreate,
    AdminUserOut,
    AdminUserUpdate,
    NavTabsOut,
    NavTabsUpdate,
)
from app.services.client_meta import client_meta
from app.services.permission_guard import (
    has_permission,
    parse_permissions,
    permission_denied,
    permissions_for_admin,
)
from app.services.security_audit import enqueue_login_event

router = APIRouter(prefix="/api/admin", tags=["admin-users"])


def _meta(request: Request) -> dict[str, str | None]:
    return client_meta(dict(request.headers), peer=request.client.host if request.client else None)


def _audit(request: Request, event_type: str, actor: str, path: str, status: int) -> None:
    enqueue_login_event(event_type, actor, _meta(request), status=status, path=path, method=request.method)


def _validate_password(password: str) -> None:
    if len(password) < MIN_ADMIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"密码至少 {MIN_ADMIN_PASSWORD_LENGTH} 位")
    if password in INSECURE_ADMIN_PASSWORDS:
        raise HTTPException(status_code=400, detail="密码过于简单，请换一个")


def _is_wildcard_holder(db: Session, admin: Admin) -> bool:
    return has_permission(permissions_for_admin(db, admin), WILDCARD)


def _wildcard_holders(db: Session) -> list[Admin]:
    admins = db.scalars(select(Admin)).all()
    return [item for item in admins if _is_wildcard_holder(db, item)]


def _count_enabled_wildcard_holders(db: Session) -> int:
    return len([item for item in _wildcard_holders(db) if item.is_enabled])


def _ensure_not_last_wildcard(db: Session, target: Admin, reason: str) -> None:
    if not _is_wildcard_holder(db, target) or not target.is_enabled:
        return
    if _count_enabled_wildcard_holders(db) <= 1:
        raise HTTPException(status_code=409, detail=f"{reason}：系统需要至少保留一个启用中的管理员")


def _ensure_target_within_scope(db: Session, actor: Admin, target: Admin) -> None:
    """非管理员只能管理权限不超出自己的账号，避免改密/停用/删除提权。"""
    actor_permissions = permissions_for_admin(db, actor)
    if WILDCARD in actor_permissions:
        return
    target_permissions = permissions_for_admin(db, target)
    if WILDCARD in target_permissions:
        raise HTTPException(
            status_code=403,
            detail={"error": {"type": "permission_denied", "message": "只有管理员可以管理管理员账号"}},
        )
    over = sorted(code for code in target_permissions if not has_permission(actor_permissions, code))
    if over:
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "type": "permission_denied",
                    "message": f"不能管理权限高于自己的账号：{'、'.join(over)}",
                    "permissions": over,
                }
            },
        )


def _ensure_role_assignable(db: Session, actor: Admin, role: Role) -> None:
    actor_permissions = permissions_for_admin(db, actor)
    if role.builtin:
        if WILDCARD in actor_permissions:
            return
        raise HTTPException(
            status_code=403,
            detail={"error": {"type": "permission_denied", "message": "只有管理员可以把账号设为管理员"}},
        )
    if WILDCARD in actor_permissions:
        return
    over = sorted(code for code in parse_permissions(role.permissions_json) if not has_permission(actor_permissions, code))
    if over:
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "type": "permission_denied",
                    "message": f"不能分配超出自身权限的角色：{'、'.join(over)}",
                    "permissions": over,
                }
            },
        )


def _load_admin(db: Session, admin_id: int) -> Admin:
    target = db.get(Admin, admin_id)
    if target is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return target


def _user_out(db: Session, admin: Admin) -> AdminUserOut:
    role = db.get(Role, admin.role_id) if admin.role_id else None
    return AdminUserOut(
        id=admin.id,
        username=admin.username,
        role_id=admin.role_id,
        role_name=role.name if role else "",
        role_builtin=bool(role.builtin) if role else False,
        is_enabled=bool(admin.is_enabled),
        created_at=admin.created_at,
        last_login_at=admin.last_login_at,
    )


@router.get("/users", response_model=list[AdminUserOut])
def list_users(db: Session = Depends(get_db)) -> list[AdminUserOut]:
    admins = db.scalars(select(Admin).order_by(Admin.id.asc())).all()
    return [_user_out(db, item) for item in admins]


@router.post("/users", response_model=AdminUserOut, status_code=201)
def create_user(
    payload: AdminUserCreate,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    _validate_password(payload.password)
    role = db.get(Role, payload.role_id)
    if role is None:
        raise HTTPException(status_code=400, detail="角色不存在")
    _ensure_role_assignable(db, admin, role)
    if db.scalar(select(Admin).where(func.lower(Admin.username) == username.lower())) is not None:
        raise HTTPException(status_code=409, detail="用户名已存在")
    record = Admin(
        username=username,
        password_hash=hash_password(payload.password),
        role_id=role.id,
        is_enabled=bool(payload.is_enabled),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    _audit(request, "admin_user_created", admin.username, f"/api/admin/users/{record.id}", 201)
    return _user_out(db, record)


@router.patch("/users/{admin_id}", response_model=AdminUserOut)
def update_user(
    admin_id: int,
    payload: AdminUserUpdate,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    target = _load_admin(db, admin_id)
    _ensure_target_within_scope(db, admin, target)
    if payload.is_enabled is not None and not has_permission(
        permissions_for_admin(db, admin), "users:toggle_status"
    ):
        raise permission_denied("users:toggle_status")
    if payload.username is not None:
        username = payload.username.strip()
        if not username:
            raise HTTPException(status_code=400, detail="用户名不能为空")
        dup = db.scalar(
            select(Admin).where(func.lower(Admin.username) == username.lower(), Admin.id != target.id)
        )
        if dup is not None:
            raise HTTPException(status_code=409, detail="用户名已存在")
        target.username = username
    if payload.role_id is not None:
        role = db.get(Role, payload.role_id)
        if role is None:
            raise HTTPException(status_code=400, detail="角色不存在")
        _ensure_role_assignable(db, admin, role)
        if role.id != target.role_id:
            _ensure_not_last_wildcard(db, target, "不能调整最后一个管理员")
        target.role_id = role.id
        # 角色变了：让该账号的旧会话失效，避免拿着旧前端状态继续用
        target.token_version = int(target.token_version or 0) + 1
    if payload.is_enabled is not None:
        if payload.is_enabled is False:
            if target.id == admin.id:
                raise HTTPException(status_code=409, detail="不能停用当前登录账号")
            _ensure_not_last_wildcard(db, target, "不能停用最后一个管理员")
            target.token_version = int(target.token_version or 0) + 1
        target.is_enabled = bool(payload.is_enabled)
    db.commit()
    db.refresh(target)
    _audit(request, "admin_user_updated", admin.username, f"/api/admin/users/{target.id}", 200)
    return _user_out(db, target)


@router.post("/users/{admin_id}/password", response_model=AdminUserOut)
def reset_password(
    admin_id: int,
    payload: AdminPasswordReset,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    target = _load_admin(db, admin_id)
    _ensure_target_within_scope(db, admin, target)
    _validate_password(payload.password)
    target.password_hash = hash_password(payload.password)
    target.token_version = int(target.token_version or 0) + 1
    db.commit()
    db.refresh(target)
    _audit(request, "admin_user_password_reset", admin.username, f"/api/admin/users/{target.id}", 200)
    return _user_out(db, target)


@router.post("/users/{admin_id}/status", response_model=AdminUserOut)
def toggle_status(
    admin_id: int,
    payload: dict,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    enabled = bool(payload.get("enabled", True))
    return update_user(
        admin_id,
        AdminUserUpdate(is_enabled=enabled),
        request,
        admin,
        db,
    )


@router.delete("/users/{admin_id}", status_code=204)
def delete_user(
    admin_id: int,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    target = _load_admin(db, admin_id)
    if target.id == admin.id:
        raise HTTPException(status_code=409, detail="不能删除当前登录账号")
    _ensure_target_within_scope(db, admin, target)
    _ensure_not_last_wildcard(db, target, "不能删除最后一个管理员")
    db.delete(target)
    db.commit()
    _audit(request, "admin_user_deleted", admin.username, f"/api/admin/users/{admin_id}", 204)


@router.get("/settings/nav-tabs", response_model=NavTabsOut)
def default_nav_tabs(admin: Admin = Depends(get_current_admin), db: Session = Depends(get_db)) -> NavTabsOut:
    return NavTabsOut(**resolve_for_admin(db, admin))


@router.put("/settings/nav-tabs", response_model=NavTabsOut)
def update_default_nav_tabs(
    payload: NavTabsUpdate,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> NavTabsOut:
    save_default(db, payload.paths)
    return NavTabsOut(**resolve_for_admin(db, admin))

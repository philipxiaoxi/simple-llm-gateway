
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.crypto import hash_password, verify_password
from app.db import get_db
from app.deps import admin_role_view, create_access_token, get_current_admin
from app.login_gate import LoginLocked, login_gate
from app.models import Admin
from app.schemas import AdminUpdateRequest, LoginRequest, LoginResponse, MeOut, MeRoleOut
from app.services.client_meta import client_meta
from app.services.security_audit import enqueue_login_event, record_auth_failure

router = APIRouter(prefix="/api/admin", tags=["admin-auth"])


def _request_meta(request: Request) -> dict[str, str | None]:
    return client_meta(
        dict(request.headers),
        peer=request.client.host if request.client else None,
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    meta = _request_meta(request)
    headers = dict(request.headers)
    client = meta.get("client_ip") or (request.client.host if request.client else "unknown")
    try:
        login_gate.check(client)
    except LoginLocked as error:
        enqueue_login_event(
            "login_locked", payload.username, meta,
            severity="warn", attempted_password=payload.password, headers=headers, status=429,
        )
        raise HTTPException(
            status_code=429,
            detail="登录失败次数过多，请稍后再试",
            headers={"Retry-After": str(error.retry_after)},
        ) from error
    admin = db.scalar(select(Admin).where(Admin.username == payload.username))
    if admin is not None and not bool(admin.is_enabled):
        enqueue_login_event(
            "login_disabled", payload.username, meta,
            severity="warn", attempted_password=payload.password, headers=headers, status=403,
        )
        raise HTTPException(status_code=403, detail="账号已停用")
    if admin is None or not verify_password(payload.password, admin.password_hash):
        login_gate.fail(client)
        enqueue_login_event(
            "login_failure", payload.username, meta,
            attempted_password=payload.password, headers=headers, status=401,
        )
        record_auth_failure(meta.get("client_ip"))
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    login_gate.succeed(client)
    enqueue_login_event("login_success", admin.username, meta, headers=headers, status=200)
    admin.last_login_at = utcnow()
    return LoginResponse(token=create_access_token(admin), username=admin.username)


@router.get("/me", response_model=MeOut)
def me(admin: Admin = Depends(get_current_admin), db: Session = Depends(get_db)) -> MeOut:
    role, permissions = admin_role_view(db, admin)
    return MeOut(
        username=admin.username,
        role=MeRoleOut(**role) if role else None,
        permissions=permissions,
    )


@router.patch("/me", response_model=LoginResponse)
def update_me(
    payload: AdminUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> LoginResponse:
    if not verify_password(payload.current_password, admin.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    new_username = (payload.username or "").strip()
    new_password = payload.password or ""
    if not new_username and not new_password:
        raise HTTPException(status_code=400, detail="请填写新用户名或新密码")
    changed = False
    if new_username:
        clash = db.scalar(select(Admin).where(Admin.username == new_username, Admin.id != admin.id))
        if clash is not None:
            raise HTTPException(status_code=400, detail="用户名已被占用")
        admin.username = new_username
        changed = True
    if new_password:
        if len(new_password) < 8:
            raise HTTPException(status_code=400, detail="新密码至少 8 位")
        admin.password_hash = hash_password(new_password)
        admin.token_version = int(admin.token_version or 0) + 1
        changed = True
    if changed:
        enqueue_login_event(
            "admin_credential_change",
            admin.username,
            _request_meta(request),
            severity="warn",
            headers=dict(request.headers),
            status=200,
            path="/api/admin/me",
            method="PATCH",
        )
    return LoginResponse(token=create_access_token(admin), username=admin.username)

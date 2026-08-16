
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.crypto import hash_password, verify_password
from app.db import get_db
from app.deps import create_access_token, get_current_admin
from app.login_gate import LoginLocked, login_gate
from app.models import Admin
from app.schemas import AdminUpdateRequest, LoginRequest, LoginResponse

router = APIRouter(prefix="/api/admin", tags=["admin-auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    try:
        login_gate.check(payload.username)
    except LoginLocked as error:
        raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后再试") from error
    admin = db.scalar(select(Admin).where(Admin.username == payload.username))
    if admin is None or not verify_password(payload.password, admin.password_hash):
        login_gate.fail(payload.username)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    login_gate.succeed(payload.username)
    admin.last_login_at = utcnow()
    return LoginResponse(token=create_access_token(admin), username=admin.username)


@router.get("/me")
def me(admin: Admin = Depends(get_current_admin)) -> dict[str, str]:
    return {"username": admin.username}


@router.patch("/me", response_model=LoginResponse)
def update_me(
    payload: AdminUpdateRequest,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> LoginResponse:
    if not verify_password(payload.current_password, admin.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    new_username = (payload.username or "").strip()
    new_password = payload.password or ""
    if not new_username and not new_password:
        raise HTTPException(status_code=400, detail="请填写新用户名或新密码")
    if new_username:
        clash = db.scalar(select(Admin).where(Admin.username == new_username, Admin.id != admin.id))
        if clash is not None:
            raise HTTPException(status_code=400, detail="用户名已被占用")
        admin.username = new_username
    if new_password:
        if len(new_password) < 8:
            raise HTTPException(status_code=400, detail="新密码至少 8 位")
        admin.password_hash = hash_password(new_password)
        admin.token_version = int(admin.token_version or 0) + 1
    return LoginResponse(token=create_access_token(admin), username=admin.username)

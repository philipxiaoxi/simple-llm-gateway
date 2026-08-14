from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crypto import verify_password
from app.db import get_db
from app.deps import create_access_token, get_current_admin
from app.models import Admin
from app.schemas import LoginRequest, LoginResponse

router = APIRouter(prefix="/api/admin", tags=["admin-auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    admin = db.scalar(select(Admin).where(Admin.username == payload.username))
    if admin is None or not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    admin.last_login_at = datetime.utcnow()
    return LoginResponse(token=create_access_token(admin.username), username=admin.username)


@router.get("/me")
def me(admin: Admin = Depends(get_current_admin)) -> dict[str, str]:
    return {"username": admin.username}

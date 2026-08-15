from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.crypto import hash_api_key
from app.db import get_db
from app.models import Admin, ApiKey


def create_access_token(admin: Admin) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_expire_days)
    payload = {
        "sub": admin.username,
        "ver": int(admin.token_version or 0),
        "exp": expire,
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm="HS256")


def get_current_admin(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Admin:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.split(" ", 1)[1].strip()
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.app_secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as error:
        raise HTTPException(status_code=401, detail="登录已失效") from error
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="登录已失效")
    admin = db.scalar(select(Admin).where(Admin.username == username))
    if admin is None:
        raise HTTPException(status_code=401, detail="账号不存在")
    token_version = int(payload.get("ver") or 0)
    if token_version != int(admin.token_version or 0):
        raise HTTPException(status_code=401, detail="登录已失效")
    return admin


def extract_raw_api_key(
    authorization: str | None = None,
    x_api_key: str | None = None,
) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return None


def resolve_api_key(db: Session, raw_key: str | None) -> ApiKey | None:
    if not raw_key:
        return None
    digest = hash_api_key(raw_key)
    return db.scalar(
        select(ApiKey).options(joinedload(ApiKey.account)).where(ApiKey.key_hash == digest)
    )

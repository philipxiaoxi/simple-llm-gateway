"""资讯公开页门禁：单例设置、解锁令牌与会话。

公开页面向部分人员开放，用一道口令门禁保护。口令用 bcrypt 存哈希；解锁后签发一个
自包含的 JWT（HttpOnly Cookie 承载），有效期固定 3 天，过期需重新输入。改口令时
`token_version` 自增，旧会话立即失效。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.crypto import hash_password, verify_password
from app.login_gate import LoginGate
from app.models import InfoPublicSession, InfoPublicSettings

COOKIE_NAME = "public_info_gate"
GATE_TTL_SECONDS = 3 * 24 * 3600
MIN_PASSWORD_LENGTH = 6
SESSION_RETENTION_DAYS = 30
_TOKEN_SCOPE = "info-public"
# 去掉易混字符（0/O/1/I/L），便于肉眼核对水印短码
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_CODE_LENGTH = 8

# 解锁接口的暴力破解限流：按客户端 IP 计数，锁定冷却 15 分钟
unlock_gate = LoginGate()


def get_public_gate_settings(db: Session) -> InfoPublicSettings:
    row = db.scalar(select(InfoPublicSettings).order_by(InfoPublicSettings.id).limit(1))
    if row is None:
        row = InfoPublicSettings(enabled=False, password_hash="", token_version=0)
        db.add(row)
        db.flush()
    return row


def has_password(row: InfoPublicSettings) -> bool:
    return bool((row.password_hash or "").strip())


def password_fingerprint(row: InfoPublicSettings) -> str:
    """口令指纹（哈希前缀）：不暴露明文，但可区分不同口令。"""
    if not has_password(row):
        return ""
    digest = hashlib.sha256(row.password_hash.encode("utf-8")).hexdigest()
    return digest[:8].upper()


def is_required(row: InfoPublicSettings) -> bool:
    """仅当既启用又已设口令时才真正拦截。"""
    return bool(row.enabled) and has_password(row)


def serialize(row: InfoPublicSettings) -> dict[str, Any]:
    return {
        "enabled": bool(row.enabled),
        "has_password": has_password(row),
        "required": is_required(row),
        "ttl_days": GATE_TTL_SECONDS // 86400,
        "password_fingerprint": password_fingerprint(row),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def set_password(row: InfoPublicSettings, password: str) -> None:
    cleaned = (password or "").strip()
    if len(cleaned) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"口令至少 {MIN_PASSWORD_LENGTH} 位")
    row.password_hash = hash_password(cleaned)
    row.enabled = True
    row.token_version = int(row.token_version or 0) + 1
    row.updated_at = utcnow()


def clear_password(row: InfoPublicSettings) -> None:
    row.password_hash = ""
    row.enabled = False
    row.token_version = int(row.token_version or 0) + 1
    row.updated_at = utcnow()


def set_enabled(row: InfoPublicSettings, enabled: bool) -> None:
    # 没有口令时无法启用，避免把自己锁在外面
    row.enabled = bool(enabled) and has_password(row)
    row.updated_at = utcnow()


def check_password(row: InfoPublicSettings, password: str) -> bool:
    if not has_password(row):
        return False
    return verify_password(password or "", row.password_hash)


def _generate_code(db: Session) -> str:
    while True:
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        exists = db.scalar(select(InfoPublicSession.id).where(InfoPublicSession.code == code))
        if exists is None:
            return code


def _prune_sessions(db: Session) -> None:
    cutoff = utcnow() - timedelta(days=SESSION_RETENTION_DAYS)
    db.execute(delete(InfoPublicSession).where(InfoPublicSession.created_at < cutoff))


def decode_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, get_settings().app_secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("scope") != _TOKEN_SCOPE:
        return None
    return payload


def verify_token(db: Session, token: str | None) -> bool:
    payload = decode_token(token)
    if payload is None:
        return False
    row = get_public_gate_settings(db)
    if not is_required(row):
        return True
    return int(payload.get("ver") or -1) == int(row.token_version or 0)


def watermark_from_token(token: str | None) -> dict[str, Any] | None:
    """解锁会话的水印信息：短码 + 签发时间。"""
    payload = decode_token(token)
    if payload is None or not payload.get("code"):
        return None
    issued_raw = payload.get("iat")
    issued_at = None
    if isinstance(issued_raw, (int, float)):
        issued_at = datetime.fromtimestamp(issued_raw, tz=timezone.utc).isoformat()
    return {"code": str(payload["code"]), "issued_at": issued_at}


def issue_session(
    db: Session, row: InfoPublicSettings, *, ip: str = "", user_agent: str = ""
) -> tuple[str, int, str]:
    """签发解锁令牌，并登记一条可溯源的会话记录。"""
    _prune_sessions(db)
    code = _generate_code(db)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=GATE_TTL_SECONDS)
    db.add(
        InfoPublicSession(
            code=code,
            ip=(ip or "")[:64],
            user_agent=(user_agent or "")[:256],
            gate_version=int(row.token_version or 0),
            password_fingerprint=password_fingerprint(row),
            created_at=utcnow(),
            expires_at=expires.replace(tzinfo=None),
        )
    )
    payload = {
        "scope": _TOKEN_SCOPE,
        "ver": int(row.token_version or 0),
        "code": code,
        "iat": int(now.timestamp()),
        "exp": expires,
    }
    token = jwt.encode(payload, get_settings().app_secret_key, algorithm="HS256")
    return token, GATE_TTL_SECONDS, code


def list_sessions(db: Session, *, code: str | None = None, limit: int = 50) -> list[InfoPublicSession]:
    size = max(1, min(200, int(limit or 50)))
    stmt = select(InfoPublicSession)
    if code:
        stmt = stmt.where(InfoPublicSession.code == code.strip().upper())
    stmt = stmt.order_by(InfoPublicSession.id.desc()).limit(size)
    return list(db.scalars(stmt).all())


def serialize_session(row: InfoPublicSession) -> dict[str, Any]:
    return {
        "code": row.code,
        "ip": row.ip,
        "user_agent": row.user_agent,
        "gate_version": int(row.gate_version or 0),
        "password_fingerprint": row.password_fingerprint or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }

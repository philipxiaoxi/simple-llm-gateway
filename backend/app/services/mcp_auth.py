from __future__ import annotations

import secrets

from fastapi import Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.crypto import encrypt_secret, hash_api_key
from app.deps import extract_raw_api_key
from app.models import McpKey, McpKeyCapability


def generate_mcp_key() -> str:
    return "mcp-" + secrets.token_urlsafe(32)


def hash_mcp_key(plaintext: str) -> str:
    return hash_api_key(plaintext)


def mcp_key_prefix(plaintext: str) -> str:
    return plaintext[:12] if len(plaintext) >= 12 else plaintext


def resolve_mcp_key(db: Session, raw_key: str | None) -> McpKey | None:
    if not raw_key:
        return None
    digest = hash_mcp_key(raw_key.strip())
    return db.scalar(
        select(McpKey)
        .where(McpKey.key_hash == digest)
        .options(selectinload(McpKey.capabilities))
    )


def allowed_capability_ids(mcp_key: McpKey) -> set[str]:
    return {item.capability_id for item in mcp_key.capabilities}


def assert_capability_allowed(mcp_key: McpKey, capability_id: str) -> None:
    if capability_id not in allowed_capability_ids(mcp_key):
        raise HTTPException(
            status_code=403,
            detail={"error": {"type": "permission_error", "message": f"MCP Key 未授权能力 {capability_id}"}},
        )


def create_mcp_key_record(db: Session, name: str, capability_ids: list[str]) -> tuple[McpKey, str]:
    unique_ids = []
    seen: set[str] = set()
    for item in capability_ids:
        cid = item.strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        unique_ids.append(cid)
    if not unique_ids:
        raise HTTPException(
            status_code=400,
            detail={"error": {"type": "invalid_request", "message": "至少选择一个 capability"}},
        )
    plaintext = generate_mcp_key()
    settings = get_settings()
    record = McpKey(
        name=name.strip(),
        key_hash=hash_mcp_key(plaintext),
        key_encrypted=encrypt_secret(plaintext, settings.app_secret_key),
        key_prefix=mcp_key_prefix(plaintext),
        status="active",
    )
    db.add(record)
    db.flush()
    for cid in unique_ids:
        db.add(McpKeyCapability(mcp_key_id=record.id, capability_id=cid))
    db.flush()
    db.refresh(record)
    return record, plaintext


def replace_mcp_key_capabilities(db: Session, mcp_key: McpKey, capability_ids: list[str]) -> None:
    unique_ids = []
    seen: set[str] = set()
    for item in capability_ids:
        cid = item.strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        unique_ids.append(cid)
    if not unique_ids:
        raise HTTPException(
            status_code=400,
            detail={"error": {"type": "invalid_request", "message": "白名单不能为空"}},
        )
    mcp_key.capabilities.clear()
    db.flush()
    for cid in unique_ids:
        db.add(McpKeyCapability(mcp_key_id=mcp_key.id, capability_id=cid))
    db.flush()


def get_mcp_key_from_headers(
    db: Session,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> McpKey:
    raw = extract_raw_api_key(authorization, x_api_key)
    mcp_key = resolve_mcp_key(db, raw)
    if mcp_key is None:
        raise HTTPException(
            status_code=401,
            detail={"error": {"type": "authentication_error", "message": "无效的 MCP Key"}},
        )
    if mcp_key.status != "active":
        raise HTTPException(
            status_code=401,
            detail={"error": {"type": "authentication_error", "message": "MCP Key 已停用"}},
        )
    return mcp_key

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.capabilities import catalog_payload, ensure_defaults
from app.crypto import decrypt_secret
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_admin
from app.models import McpKey
from app.schemas import McpKeyCapabilitiesUpdate, McpKeyCreate, McpKeyOut, McpKeyUpdate
from app.services.mcp_auth import allowed_capability_ids, create_mcp_key_record, replace_mcp_key_capabilities

router = APIRouter(prefix="/api/admin/mcp/keys", tags=["admin-mcp-keys"], dependencies=[Depends(get_current_admin)])


def _out(item: McpKey, key: str | None = None) -> McpKeyOut:
    return McpKeyOut(
        id=item.id,
        name=item.name,
        key_prefix=item.key_prefix,
        status=item.status,
        capability_ids=[cap.capability_id for cap in item.capabilities],
        created_at=item.created_at,
        last_used_at=item.last_used_at,
        key=key,
    )


@router.get("", response_model=list[McpKeyOut])
def list_keys(db: Session = Depends(get_db)):
    items = db.scalars(select(McpKey).options(selectinload(McpKey.capabilities)).order_by(McpKey.id.desc())).all()
    return [_out(item) for item in items]


@router.post("", response_model=McpKeyOut, status_code=201)
def create_key(payload: McpKeyCreate, db: Session = Depends(get_db)):
    record, plaintext = create_mcp_key_record(db, payload.name, payload.capability_ids)
    db.refresh(record)
    record = db.scalar(select(McpKey).where(McpKey.id == record.id).options(selectinload(McpKey.capabilities)))
    assert record is not None
    return _out(record, key=plaintext)


@router.patch("/{key_id}", response_model=McpKeyOut)
def update_key(key_id: int, payload: McpKeyUpdate, db: Session = Depends(get_db)):
    item = db.scalar(select(McpKey).where(McpKey.id == key_id).options(selectinload(McpKey.capabilities)))
    if item is None:
        raise HTTPException(404, "MCP Key 不存在")
    if payload.name is not None:
        item.name = payload.name.strip()
    if payload.status is not None:
        status = payload.status.strip().lower()
        if status not in {"active", "disabled"}:
            raise HTTPException(400, "status 只能是 active 或 disabled")
        item.status = status
    db.flush()
    db.refresh(item)
    return _out(item)


@router.put("/{key_id}/capabilities", response_model=McpKeyOut)
def put_capabilities(key_id: int, payload: McpKeyCapabilitiesUpdate, db: Session = Depends(get_db)):
    item = db.scalar(select(McpKey).where(McpKey.id == key_id).options(selectinload(McpKey.capabilities)))
    if item is None:
        raise HTTPException(404, "MCP Key 不存在")
    replace_mcp_key_capabilities(db, item, payload.capability_ids)
    item = db.scalar(select(McpKey).where(McpKey.id == key_id).options(selectinload(McpKey.capabilities)))
    assert item is not None
    return _out(item)


@router.get("/{key_id}/reveal")
def reveal_key(key_id: int, db: Session = Depends(get_db)):
    """管理员可再次查看完整 MCP Key，便于复制到下游配置。"""
    item = db.get(McpKey, key_id)
    if item is None:
        raise HTTPException(404, "MCP Key 不存在")
    try:
        plaintext = decrypt_secret(item.key_encrypted, get_settings().app_secret_key)
    except ValueError as error:
        raise HTTPException(409, "无法解密该 Key，APP_SECRET_KEY 可能已更换") from error
    return {"id": item.id, "name": item.name, "key": plaintext}


@router.get("/{key_id}/integration")
def key_integration(key_id: int, request: Request, db: Session = Depends(get_db)):
    """按 Key 生成接入中心所需的端点、能力清单与授权标记，不返回明文密钥。"""
    item = db.scalar(select(McpKey).where(McpKey.id == key_id).options(selectinload(McpKey.capabilities)))
    if item is None:
        raise HTTPException(404, "MCP Key 不存在")
    ensure_defaults()
    allowed = allowed_capability_ids(item)
    origin = (get_settings().app_base_url or str(request.base_url)).rstrip("/")
    capabilities = []
    for row in catalog_payload(only_enabled=False):
        entry = dict(row)
        entry["authorized"] = entry["capability_id"] in allowed
        capabilities.append(entry)
    return {
        "origin": origin,
        "mcp_url": f"{origin}/mcp",
        "rest_base_url": origin,
        "key": {
            "id": item.id,
            "name": item.name,
            "key_prefix": item.key_prefix,
            "status": item.status,
            "capability_ids": sorted(allowed),
        },
        "capabilities": capabilities,
    }


@router.delete("/{key_id}", status_code=204)
def delete_key(key_id: int, db: Session = Depends(get_db)):
    item = db.get(McpKey, key_id)
    if item is None:
        raise HTTPException(404, "MCP Key 不存在")
    db.delete(item)
    db.flush()

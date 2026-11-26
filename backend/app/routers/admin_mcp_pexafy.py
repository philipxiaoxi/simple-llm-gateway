from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.capabilities import pexafy
from app.capabilities.base import CapabilityError
from app.config import get_settings
from app.crypto import encrypt_secret
from app.db import get_db
from app.models import PexafySettings
from app.services.permission_guard import require_permission

router = APIRouter(prefix="/api/admin/mcp/pexafy", tags=["admin-mcp-pexafy"])
Database = Annotated[Session, Depends(get_db)]


class SettingsInput(BaseModel):
    api_key: str | None = Field(max_length=2048)


@router.get("/settings", dependencies=[Depends(require_permission("mcp_pexafy:view"))])
def settings(db: Database):
    row = db.get(PexafySettings, 1)
    return {"configured": bool(row and row.api_key_encrypted)}


@router.put("/settings", dependencies=[Depends(require_permission("mcp_pexafy:configure"))])
def save_settings(payload: SettingsInput, db: Database):
    key = payload.api_key.strip() if payload.api_key is not None else None
    if key is not None and (
        not key or not key.isascii() or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in key)
    ):
        raise HTTPException(422, "API Key 格式无效")
    row = db.get(PexafySettings, 1)
    if row is None:
        row = PexafySettings(id=1)
        db.add(row)
    row.api_key_encrypted = encrypt_secret(key, get_settings().app_secret_key) if key else None
    return {"configured": bool(key)}


@router.get("/usage", dependencies=[Depends(require_permission("mcp_pexafy:view"))])
async def usage(db: Database):
    try:
        key = pexafy.credential(db)
        db.commit()
        return await pexafy.api_call(key, "GET", "/usage")
    except CapabilityError as error:
        raise HTTPException(error.status_code, error.message) from None

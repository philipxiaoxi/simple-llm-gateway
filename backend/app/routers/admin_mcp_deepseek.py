from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, PositiveInt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.capabilities.deepseek_web import eligible_account
from app.db import get_db
from app.models import DeepSeekWebSettings, UpstreamAccount
from app.services.permission_guard import require_permission

router = APIRouter(prefix="/api/admin/mcp/deepseek-web", tags=["admin-mcp-deepseek"])
Database = Annotated[Session, Depends(get_db)]


class SettingsInput(BaseModel):
    account_id: PositiveInt | None


@router.get("/settings", dependencies=[Depends(require_permission("mcp_deepseek:view"))])
def settings(db: Database):
    row = db.get(DeepSeekWebSettings, 1)
    accounts = db.scalars(select(UpstreamAccount).where(
        UpstreamAccount.provider == "deepseek", UpstreamAccount.source == "upstream",
    ).order_by(UpstreamAccount.name, UpstreamAccount.id))
    return {"account_id": row.account_id if row else None, "accounts": [
        {"id": account.id, "name": account.name, "available": eligible_account(account)} for account in accounts
    ]}


@router.put("/settings", dependencies=[Depends(require_permission("mcp_deepseek:configure"))])
def save_settings(payload: SettingsInput, db: Database):
    if payload.account_id is not None and not eligible_account(db.get(UpstreamAccount, payload.account_id)):
        raise HTTPException(400, "请选择已启用且已配置密钥的 DeepSeek 上游账号")
    row = db.get(DeepSeekWebSettings, 1)
    if row is None:
        row = DeepSeekWebSettings(id=1)
        db.add(row)
    row.account_id = payload.account_id
    return {"account_id": row.account_id}

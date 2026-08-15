from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.crypto import encrypt_secret
from app.db import get_db
from app.deps import get_current_admin
from app.models import Admin, UpstreamAccount
from app.providers import get_provider, list_providers
from app.schemas import AccountCreate, AccountExportRequest, AccountImportRequest, AccountOut, AccountUpdate, ProviderOut
from app.serializers import account_to_out
from app.services.account_transfer import export_accounts, import_accounts
from app.services.probe import list_account_models, probe_account
from app.services.quota import refresh_quota

router = APIRouter(prefix="/api/admin", tags=["admin-accounts"], dependencies=[Depends(get_current_admin)])


@router.get("/providers", response_model=list[ProviderOut])
def admin_list_providers() -> list[ProviderOut]:
    return [
        ProviderOut(
            id=provider.id,
            label=provider.label,
            auth_type=provider.auth_type,
            base_url=provider.default_base_url,
            models=list(provider.default_models),
        )
        for provider in list_providers()
    ]


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)) -> list[AccountOut]:
    rows = db.scalars(select(UpstreamAccount).options(joinedload(UpstreamAccount.oauth_token)).order_by(UpstreamAccount.id)).all()
    return [account_to_out(row) for row in rows]


@router.post("/accounts", response_model=AccountOut)
def create_account(
    payload: AccountCreate,
    db: Session = Depends(get_db),
    _: Admin = Depends(get_current_admin),
) -> AccountOut:
    try:
        provider = get_provider(payload.provider)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    encrypted = None
    if payload.api_key:
        encrypted = encrypt_secret(payload.api_key, get_settings().app_secret_key)
    account = UpstreamAccount(
        name=payload.name,
        provider=payload.provider,
        auth_type=provider.auth_type,
        base_url=(payload.base_url or "").strip() or provider.default_base_url,
        api_key_encrypted=encrypted,
        status=payload.status,
        updated_at=datetime.utcnow(),
    )
    db.add(account)
    db.flush()
    initial_quota = provider.initial_quota()
    if initial_quota is not None:
        provider.store_quota(account, initial_quota)
    return account_to_out(account)


@router.post("/accounts/export")
def export_upstream_accounts(payload: AccountExportRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return export_accounts(db, payload.password)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/accounts/import")
def import_upstream_accounts(payload: AccountImportRequest, db: Session = Depends(get_db)) -> dict[str, int]:
    try:
        return import_accounts(db, payload.password, payload.payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/accounts/{account_id}", response_model=AccountOut)
def get_account(
    account_id: int,
    reveal: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> AccountOut:
    account = _get_account(db, account_id)
    return account_to_out(account, reveal=reveal)


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(
    account_id: int,
    payload: AccountUpdate,
    db: Session = Depends(get_db),
) -> AccountOut:
    account = _get_account(db, account_id)
    if payload.name is not None:
        account.name = payload.name
    if payload.base_url is not None:
        stripped = payload.base_url.strip()
        if stripped:
            account.base_url = stripped
    if payload.status is not None:
        account.status = payload.status
    if payload.api_key:
        account.api_key_encrypted = encrypt_secret(payload.api_key, get_settings().app_secret_key)
    account.updated_at = datetime.utcnow()
    return account_to_out(account)


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    account = _get_account(db, account_id)
    if account.api_keys:
        raise HTTPException(status_code=400, detail="请先删除绑定在该账号上的 API Key")
    if account.oauth_token is not None:
        db.delete(account.oauth_token)
    db.delete(account)
    return {"ok": True}


@router.post("/accounts/{account_id}/probe")
async def probe(account_id: int, db: Session = Depends(get_db)) -> dict:
    account = _get_account(db, account_id)
    return await probe_account(account)


@router.post("/accounts/{account_id}/quota")
async def quota(account_id: int, db: Session = Depends(get_db)) -> dict:
    account = _get_account(db, account_id)
    return await refresh_quota(account)


@router.post("/accounts/{account_id}/models")
async def models(account_id: int, db: Session = Depends(get_db)) -> dict:
    account = _get_account(db, account_id)
    return await list_account_models(account)


def _get_account(db: Session, account_id: int) -> UpstreamAccount:
    account = db.scalar(
        select(UpstreamAccount)
        .options(joinedload(UpstreamAccount.oauth_token), joinedload(UpstreamAccount.api_keys))
        .where(UpstreamAccount.id == account_id)
    )
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return account

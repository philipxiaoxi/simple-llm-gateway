from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.crypto import encrypt_secret, generate_api_key, hash_api_key, key_prefix
from app.db import get_db
from app.deps import get_current_admin
from app.models import ApiKey, UpstreamAccount
from app.schemas import KeyCreate, KeyOut, KeyUpdate
from app.serializers import key_to_out

router = APIRouter(prefix="/api/admin/keys", tags=["admin-keys"], dependencies=[Depends(get_current_admin)])


@router.get("", response_model=list[KeyOut])
def list_keys(db: Session = Depends(get_db)) -> list[KeyOut]:
    rows = db.scalars(select(ApiKey).options(joinedload(ApiKey.account)).order_by(ApiKey.id.desc())).all()
    return [key_to_out(row) for row in rows]


@router.post("", response_model=KeyOut)
def create_key(payload: KeyCreate, db: Session = Depends(get_db)) -> KeyOut:
    account = db.get(UpstreamAccount, payload.account_id)
    if account is None:
        raise HTTPException(status_code=400, detail="上游账号不存在")
    plaintext = generate_api_key()
    settings = get_settings()
    item = ApiKey(
        name=payload.name,
        key_hash=hash_api_key(plaintext),
        key_encrypted=encrypt_secret(plaintext, settings.app_secret_key),
        key_prefix=key_prefix(plaintext),
        account_id=account.id,
    )
    db.add(item)
    db.flush()
    db.refresh(item)
    item.account = account
    result = key_to_out(item, reveal=True)
    result.key = plaintext
    return result


@router.get("/{key_id}", response_model=KeyOut)
def get_key(key_id: int, db: Session = Depends(get_db)) -> KeyOut:
    item = _get_key(db, key_id)
    return key_to_out(item, reveal=True)


@router.patch("/{key_id}", response_model=KeyOut)
def update_key(key_id: int, payload: KeyUpdate, db: Session = Depends(get_db)) -> KeyOut:
    item = _get_key(db, key_id)
    if payload.name is not None:
        item.name = payload.name
    if payload.status is not None:
        item.status = payload.status
    return key_to_out(item)


@router.delete("/{key_id}")
def delete_key(key_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    item = _get_key(db, key_id)
    db.delete(item)
    return {"ok": True}


def _get_key(db: Session, key_id: int) -> ApiKey:
    item = db.scalar(select(ApiKey).options(joinedload(ApiKey.account)).where(ApiKey.id == key_id))
    if item is None:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return item

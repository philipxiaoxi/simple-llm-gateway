"""公开页门禁的管理端接口（按 scope 管理：info / offline）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import info_public_gate as gate

router = APIRouter(prefix="/api/admin/public-gate", tags=["admin-public-gate"])


class PublicGateBody(BaseModel):
    enabled: bool | None = None
    password: str | None = None
    clear_password: bool | None = None


def _ensure_scope(scope: str) -> None:
    if scope not in gate.SCOPES:
        raise HTTPException(status_code=404, detail="未知的门禁范围")


@router.get("")
def get_public_gate(scope: str = Query(default=gate.DEFAULT_SCOPE), db: Session = Depends(get_db)) -> dict:
    _ensure_scope(scope)
    row = gate.get_public_gate_settings(db, scope)
    db.commit()
    return gate.serialize(row)


@router.put("")
def update_public_gate(
    payload: PublicGateBody,
    scope: str = Query(default=gate.DEFAULT_SCOPE),
    db: Session = Depends(get_db),
) -> dict:
    _ensure_scope(scope)
    provided = payload.model_fields_set
    try:
        row = gate.apply_update(
            db,
            scope,
            enabled=payload.enabled if "enabled" in provided else None,
            password=payload.password if "password" in provided else None,
            clear=bool(payload.clear_password) if "clear_password" in provided else False,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail={"error": {"type": "invalid_request", "message": str(error)}},
        ) from error
    return gate.serialize(row)


@router.get("/sessions")
def list_public_sessions(
    scope: str = Query(default=gate.DEFAULT_SCOPE),
    code: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> dict:
    _ensure_scope(scope)
    rows = gate.list_sessions(db, scope, code=code, limit=limit)
    return {"scope": scope, "sessions": [gate.serialize_session(row) for row in rows]}

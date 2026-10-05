from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_admin
from app.services import tikhub_config
from app.services.tikhub_config import TikHubConfigError

router = APIRouter(
    prefix="/api/admin/integrations",
    tags=["admin-integrations"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("/tikhub")
def get_tikhub(db: Session = Depends(get_db)):
    return tikhub_config.tikhub_status(db)


@router.put("/tikhub")
def set_tikhub(payload: dict[str, Any] | None = None, db: Session = Depends(get_db)):
    body = payload or {}
    try:
        tikhub_config.set_tikhub_config(
            db, base_url=body.get("base_url"), api_key=body.get("api_key")
        )
    except TikHubConfigError as error:
        raise HTTPException(
            status_code=400,
            detail={"error": {"type": "invalid_request", "message": str(error)}},
        ) from error
    db.commit()
    return tikhub_config.tikhub_status(db)


@router.delete("/tikhub")
def clear_tikhub(db: Session = Depends(get_db)):
    tikhub_config.clear_tikhub_config(db)
    db.commit()
    return tikhub_config.tikhub_status(db)

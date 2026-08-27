from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_admin
from app.schemas import LeaderboardOut
from app.services.leaderboard import LeaderboardError, get_leaderboard

router = APIRouter(
    prefix="/api/admin/leaderboard",
    tags=["admin-leaderboard"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=LeaderboardOut)
async def read_leaderboard(
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> LeaderboardOut:
    try:
        payload = await get_leaderboard(db, force=refresh)
    except LeaderboardError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return LeaderboardOut.model_validate(payload)

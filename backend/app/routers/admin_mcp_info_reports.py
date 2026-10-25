"""资讯上报历史：查看 Agent 经 MCP/REST 上送过的资讯，便于回溯。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_admin
from app.models import InfoItem

router = APIRouter(
    prefix="/api/admin/mcp/info-reports",
    tags=["admin-mcp-info-reports"],
    dependencies=[Depends(get_current_admin)],
)


def _title(text: str) -> str:
    first = (text or "").strip().split("\n", 1)[0].strip()
    return first[:120]


def _payload(row: InfoItem) -> dict:
    return {
        "id": row.id,
        "title": _title(row.text),
        "excerpt": (row.excerpt or row.text or "")[:200],
        "author_name": row.author_name or "",
        "kind": row.kind,
        "status": row.status,
        "media_count": int(row.media_count or 0),
        "is_featured": bool(row.is_featured),
        "is_hidden": bool(row.is_hidden),
        "ai_status": row.ai_status,
        "ai_label": row.ai_label,
        "permalink": row.permalink or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("")
def list_reports(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    filters = [InfoItem.source_type == "report"]
    keyword = (q or "").strip()
    if keyword:
        filters.append(func.coalesce(InfoItem.text, "").ilike(f"%{keyword}%"))
    total = int(db.scalar(select(func.count()).select_from(InfoItem).where(*filters)) or 0)
    rows = list(
        db.scalars(
            select(InfoItem)
            .where(*filters)
            .order_by(InfoItem.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return {
        "items": [_payload(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }

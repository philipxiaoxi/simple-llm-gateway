from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import InfoItem

CJK = re.compile(r"[\u4e00-\u9fff]")
TOKENS = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+")
TIME_WINDOWS = {"day": 1, "week": 7, "month": 30, "year": 365}


def grams(value: str) -> list[str]:
    """把中文切成重叠二元组，英文按词切分。用于弥补 FTS5 不切分中文。"""
    output: list[str] = []
    for token in TOKENS.findall(value or ""):
        if CJK.match(token):
            if len(token) == 1:
                output.append(token)
            else:
                output.extend(token[index : index + 2] for index in range(len(token) - 1))
        else:
            output.append(token.lower())
    return output


def _match_expression(query: str) -> str:
    tokens = grams(query)
    if not tokens:
        return ""
    parts = " AND ".join(f'"{token}"' for token in tokens)
    return f"grams:({parts})"


def _cutoff(time_range: str) -> datetime | None:
    days = TIME_WINDOWS.get((time_range or "").lower())
    if not days:
        return None
    return (datetime.now(UTC).replace(tzinfo=None)) - timedelta(days=days)


def index_item(db: Session, item: InfoItem) -> None:
    delete_item(db, item.id)
    content = " ".join(part for part in (item.excerpt, item.text, item.author_name) if part)
    db.execute(
        text(
            """
            INSERT INTO info_items_fts (excerpt, text, grams, author_name, item_id)
            VALUES (:excerpt, :text, :grams, :author_name, :item_id)
            """
        ),
        {
            "excerpt": item.excerpt or "",
            "text": item.text or "",
            "grams": " ".join(grams(content)),
            "author_name": item.author_name or "",
            "item_id": item.id,
        },
    )


def delete_item(db: Session, item_id: str) -> None:
    db.execute(text("DELETE FROM info_items_fts WHERE item_id = :item_id"), {"item_id": item_id})


def count_indexed(db: Session) -> int:
    return int(db.execute(text("SELECT count(*) FROM info_items_fts")).scalar() or 0)


def ensure_indexed(db: Session) -> None:
    """索引为空但库里已有内容时补建，避免首次升级后搜索为空。"""
    if count_indexed(db) > 0:
        return
    reindex_all(db)


def reindex_all(db: Session) -> int:
    db.execute(text("DELETE FROM info_items_fts"))
    rows = db.query(InfoItem).all()
    for item in rows:
        content = " ".join(part for part in (item.excerpt, item.text, item.author_name) if part)
        db.execute(
            text(
                """
                INSERT INTO info_items_fts (excerpt, text, grams, author_name, item_id)
                VALUES (:excerpt, :text, :grams, :author_name, :item_id)
                """
            ),
            {
                "excerpt": item.excerpt or "",
                "text": item.text or "",
                "grams": " ".join(grams(content)),
                "author_name": item.author_name or "",
                "item_id": item.id,
            },
        )
    return len(rows)


def search(
    db: Session,
    query: str,
    *,
    top_k: int = 8,
    time_range: str = "any",
    site: str = "",
) -> list[dict[str, Any]]:
    expression = _match_expression(query)
    if not expression:
        return []
    ensure_indexed(db)

    clauses = ["info_items_fts MATCH :expr", "items.is_hidden = 0"]
    params: dict[str, Any] = {"expr": expression, "limit": top_k}
    cutoff = _cutoff(time_range)
    if cutoff is not None:
        clauses.append("items.published_at >= :cutoff")
        params["cutoff"] = cutoff
    if site:
        clauses.append("items.permalink LIKE :site")
        params["site"] = f"%{site.strip()}%"

    rows = (
        db.execute(
            text(
                f"""
                SELECT info_items_fts.item_id AS item_id,
                       bm25(info_items_fts, 3.0, 0.0, 1.0, 1.5) AS rank,
                       items.permalink AS permalink,
                       items.author_name AS author_name,
                       items.source_type AS source_type,
                       items.published_at AS published_at,
                       items.excerpt AS excerpt
                FROM info_items_fts
                JOIN info_items AS items ON items.id = info_items_fts.item_id
                WHERE {' AND '.join(clauses)}
                ORDER BY rank
                LIMIT :limit
                """
            ),
            params,
        )
        .mappings()
        .all()
    )
    results: list[dict[str, Any]] = []
    for row in rows:
        rank = float(row.get("rank") or 0.0)
        results.append(
            {
                "item_id": row["item_id"],
                "title": (row.get("excerpt") or "")[:120],
                "snippet": row.get("excerpt") or "",
                "url": row.get("permalink") or "",
                "source": row.get("source_type") or "info",
                "author": row.get("author_name") or "",
                "published_at": row.get("published_at"),
                "score": 1.0 / (1.0 + abs(rank)),
                "provider": "local",
            }
        )
    return results

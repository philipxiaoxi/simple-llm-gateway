from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk


def insert_chunk(db: Session, chunk: KnowledgeChunk) -> None:
    db.execute(
        text(
            """
            INSERT INTO knowledge_chunks_fts (text, chunk_id, kb_id, document_id, source_name)
            VALUES (:text, :chunk_id, :kb_id, :document_id, :source_name)
            """
        ),
        {
            "text": chunk.text,
            "chunk_id": chunk.id,
            "kb_id": chunk.kb_id,
            "document_id": chunk.document_id,
            "source_name": chunk.source_name,
        },
    )


def delete_chunk_ids(db: Session, chunk_ids: list[str]) -> None:
    for chunk_id in chunk_ids:
        db.execute(text("DELETE FROM knowledge_chunks_fts WHERE chunk_id = :chunk_id"), {"chunk_id": chunk_id})


def match_expression(query: str) -> str:
    cleaned = " ".join(query.split()).replace('"', " ").strip()
    if not cleaned:
        return ""
    tokens = [token for token in cleaned.split() if token]
    return " OR ".join(f'"{token}"' for token in tokens) or f'"{cleaned}"'


def search_fts(db: Session, kb_ids: list[str], query: str, top_k: int) -> list[dict]:
    assert kb_ids, "kb_ids 不能为空"
    match_expr = match_expression(query)
    if not match_expr:
        return []
    placeholders = ", ".join(f":kb_{index}" for index in range(len(kb_ids)))
    params: dict[str, object] = {"q": match_expr, "limit": top_k}
    for index, kb_id in enumerate(kb_ids):
        params[f"kb_{index}"] = kb_id
    rows = db.execute(
        text(
            f"""
            SELECT chunk_id, document_id, kb_id, source_name, text, bm25(knowledge_chunks_fts) AS rank
            FROM knowledge_chunks_fts
            WHERE knowledge_chunks_fts MATCH :q AND kb_id IN ({placeholders})
            ORDER BY rank
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()
    return [dict(row) for row in rows]

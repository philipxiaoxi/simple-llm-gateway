from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import KnowledgeBase
from app.services import chroma_store
from app.services.chroma_store import ChromaSignatureMismatch
from app.services.embedding import EmbeddingError, get_embedding_client_for_base
from app.services.knowledge import fts
from app.services.knowledge.access import Access, assert_can_access
from app.services.knowledge.bases import base_signature, get_base
from app.services.knowledge.errors import KnowledgeError, SearchHit

RRF_K = 60


def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + "…"


def fulltext_hits(db: Session, kb_ids: list[str], query: str, top_k: int) -> list[SearchHit]:
    rows = fts.search_fts(db, kb_ids, query, top_k)
    hits: list[SearchHit] = []
    for row in rows:
        rank = float(row.get("rank") or 0.0)
        hits.append(
            SearchHit(
                chunk_id=str(row["chunk_id"]),
                document_id=str(row["document_id"]),
                text=str(row["text"] or ""),
                score=1.0 / (1.0 + abs(rank)),
                source_name=str(row["source_name"] or ""),
                kb_id=str(row["kb_id"] or ""),
            )
        )
    return hits


async def vector_hits(db: Session, base: KnowledgeBase, query: str, top_k: int) -> list[SearchHit]:
    signature = base_signature(base)
    try:
        client = get_embedding_client_for_base(db, base)
        vectors = await client.embed([query])
    except EmbeddingError as error:
        raise KnowledgeError(error.message, status_code=503, error_type="vector_unavailable") from error
    try:
        raw = chroma_store.query_chunks(
            base.id,
            signature=signature,
            embedding=vectors[0],
            top_k=top_k,
        )
    except ChromaSignatureMismatch as error:
        raise KnowledgeError(
            f"{error.expected} != {error.actual}；请对知识库执行「重建全部向量」",
            status_code=409,
            error_type="embedding_mismatch",
        ) from error
    return [
        SearchHit(
            chunk_id=item["chunk_id"],
            document_id=item["document_id"],
            text=item["text"],
            score=float(item["score"]),
            source_name=item["source_name"],
            kb_id=base.id,
        )
        for item in raw
    ]


def rrf_merge(ranked_lists: list[list[SearchHit]], top_k: int, k: int = RRF_K) -> list[SearchHit]:
    """倒数排名融合：只依赖名次，避免不同打分体系不可比。"""
    scores: dict[str, float] = {}
    representative: dict[str, SearchHit] = {}
    for hits in ranked_lists:
        for rank, hit in enumerate(hits, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
            representative.setdefault(hit.chunk_id, hit)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]
    merged: list[SearchHit] = []
    for chunk_id, score in ordered:
        hit = representative[chunk_id]
        merged.append(
            SearchHit(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                text=hit.text,
                score=score,
                source_name=hit.source_name,
                kb_id=hit.kb_id,
            )
        )
    return merged


def resolve_kb_ids(db: Session, access: Access, kb_id: str | None, kb_ids: list[str] | None) -> list[KnowledgeBase]:
    requested: list[str] = []
    if kb_ids:
        requested.extend(item.strip() for item in kb_ids if item and item.strip())
    if kb_id:
        requested.extend(item.strip() for item in kb_id.split(",") if item.strip())
    seen: set[str] = set()
    unique: list[str] = []
    for item in requested:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    if not unique:
        raise KnowledgeError("请提供 kb_id 或 kb_ids")
    bases: list[KnowledgeBase] = []
    for item in unique:
        base = get_base(db, item)
        assert_can_access(db, access, base)
        bases.append(base)
    return bases


async def search(
    db: Session,
    access: Access,
    *,
    kb_id: str | None = None,
    kb_ids: list[str] | None = None,
    query: str,
    mode: str = "hybrid",
    top_k: int = 5,
) -> dict:
    bases = resolve_kb_ids(db, access, kb_id, kb_ids)
    q = (query or "").strip()
    if not q:
        raise KnowledgeError("query 不能为空")
    mode_normalized = (mode or "hybrid").strip().lower()
    if mode_normalized not in {"vector", "fulltext", "hybrid"}:
        raise KnowledgeError("mode 必须是 vector / fulltext / hybrid")
    limit = max(1, min(int(top_k or 5), 20))
    ids = [base.id for base in bases]

    degraded = False
    degraded_reason: str | None = None
    hits: list[SearchHit] = []

    if mode_normalized == "fulltext":
        hits = fulltext_hits(db, ids, q, limit)
    elif mode_normalized == "vector":
        per_base: list[list[SearchHit]] = []
        for base in bases:
            per_base.append(await vector_hits(db, base, q, limit))
        hits = rrf_merge(per_base, limit) if len(per_base) > 1 else (per_base[0] if per_base else [])
    else:
        fts_hits = fulltext_hits(db, ids, q, limit)
        try:
            per_base = [await vector_hits(db, base, q, limit) for base in bases]
            vec_hits = rrf_merge(per_base, limit) if len(per_base) > 1 else (per_base[0] if per_base else [])
            hits = rrf_merge([vec_hits, fts_hits], limit)
        except KnowledgeError as error:
            if error.error_type in {"vector_unavailable", "embedding_mismatch"}:
                degraded = True
                degraded_reason = error.message
                hits = fts_hits
            else:
                raise

    max_chars = get_settings().mcp_knowledge_max_hit_chars
    return {
        "kb_ids": ids,
        "kb_id": ids[0] if len(ids) == 1 else None,
        "mode": mode_normalized,
        "degraded": degraded,
        "degraded_reason": degraded_reason,
        "hits": [
            {
                "chunk_id": hit.chunk_id,
                "document_id": hit.document_id,
                "kb_id": hit.kb_id,
                "text": _truncate(hit.text, max_chars),
                "score": hit.score,
                "source_name": hit.source_name,
            }
            for hit in hits
        ],
    }

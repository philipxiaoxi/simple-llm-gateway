from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models import (
    KnowledgeBase,
    KnowledgeBaseKey,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIngestJob,
    UpstreamAccount,
)
from app.services import chroma_store
from app.services.embedding import embedding_signature
from app.services.knowledge.access import Access, allowed_key_ids, assert_can_access, assert_scope_valid
from app.services.knowledge.common import REEMBED_STATUSES, account_name, new_id
from app.services.knowledge.errors import KnowledgeError


def validate_embedding_binding(
    db: Session,
    account_id: int | None,
    model: str | None,
    dimensions: int | None,
) -> tuple[int | None, str | None, int | None]:
    cleaned_model = (model or "").strip() or None
    if dimensions is not None:
        parsed = int(dimensions)
        if parsed < 32 or parsed > 4096:
            raise KnowledgeError("向量维度需要在 32–4096 之间")
        dimensions = parsed
    if account_id is None:
        return None, cleaned_model, dimensions
    account = db.get(UpstreamAccount, account_id)
    if account is None:
        raise KnowledgeError("上游账号不存在")
    if account.status != "active":
        raise KnowledgeError(f"上游账号「{account.name}」未启用")
    return account_id, cleaned_model, dimensions


def base_signature(base: KnowledgeBase) -> str:
    return embedding_signature(base.embedding_model, base.embedding_dimensions)


def list_bases(db: Session, access: Access) -> list[dict]:
    allowed = access.allowed_kb_ids(db)
    stmt = select(KnowledgeBase).order_by(KnowledgeBase.updated_at.desc())
    if allowed is not None:
        if not allowed:
            return []
        stmt = stmt.where(KnowledgeBase.id.in_(allowed))
    bases = db.scalars(stmt).all()
    result = []
    for base in bases:
        doc_count = db.scalar(
            select(func.count()).select_from(KnowledgeDocument).where(KnowledgeDocument.kb_id == base.id)
        ) or 0
        chunk_count = db.scalar(
            select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.kb_id == base.id)
        ) or 0
        active_jobs = db.scalar(
            select(func.count())
            .select_from(KnowledgeIngestJob)
            .where(
                KnowledgeIngestJob.kb_id == base.id,
                KnowledgeIngestJob.status.in_(("queued", "running")),
            )
        ) or 0
        last_job_status = db.scalar(
            select(KnowledgeIngestJob.status)
            .where(KnowledgeIngestJob.kb_id == base.id)
            .order_by(KnowledgeIngestJob.id.desc())
            .limit(1)
        )
        stale_count = db.scalar(
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(
                KnowledgeDocument.kb_id == base.id,
                KnowledgeDocument.vector_status.in_(REEMBED_STATUSES),
            )
        ) or 0
        actual_signature = chroma_store.collection_signature(base.id) if chunk_count else None
        result.append(
            {
                "id": base.id,
                "name": base.name,
                "description": base.description,
                "scope": base.scope,
                "embedding_account_id": base.embedding_account_id,
                "embedding_account_name": account_name(db, base.embedding_account_id),
                "embedding_model": base.embedding_model,
                "embedding_dimensions": base.embedding_dimensions,
                "allowed_mcp_key_ids": allowed_key_ids(db, base.id) if base.scope == "restricted" else [],
                "document_count": int(doc_count),
                "chunk_count": int(chunk_count),
                "stale_document_count": int(stale_count),
                "signature_mismatch": bool(chunk_count) and actual_signature != base_signature(base),
                "active_job_count": int(active_jobs),
                "last_job_status": last_job_status,
                "created_at": base.created_at,
                "updated_at": base.updated_at,
            }
        )
    return result


def create_base(
    db: Session,
    name: str,
    description: str = "",
    *,
    scope: str = "public",
    embedding_account_id: int | None = None,
    embedding_model: str | None = None,
    embedding_dimensions: int | None = None,
    allowed_mcp_key_ids: list[int] | None = None,
) -> KnowledgeBase:
    account_id, model, dimensions = validate_embedding_binding(
        db, embedding_account_id, embedding_model, embedding_dimensions
    )
    base = KnowledgeBase(
        id=new_id(),
        name=name.strip(),
        description=(description or "").strip(),
        scope=assert_scope_valid(scope),
        embedding_account_id=account_id,
        embedding_model=model,
        embedding_dimensions=dimensions,
    )
    db.add(base)
    db.flush()
    if base.scope == "restricted" and allowed_mcp_key_ids:
        for key_id in set(allowed_mcp_key_ids):
            db.add(KnowledgeBaseKey(kb_id=base.id, mcp_key_id=int(key_id)))
    db.flush()
    db.refresh(base)
    return base


def get_base(db: Session, kb_id: str) -> KnowledgeBase:
    base = db.get(KnowledgeBase, kb_id)
    if base is None:
        raise KnowledgeError("知识库不存在", status_code=404, error_type="not_found")
    return base


def get_base_for_access(db: Session, kb_id: str, access: Access) -> KnowledgeBase:
    base = get_base(db, kb_id)
    assert_can_access(db, access, base)
    return base


def update_base(
    db: Session,
    kb_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    scope: str | None = None,
    embedding_account_id: int | None = None,
    embedding_model: str | None = None,
    embedding_dimensions: int | None = None,
    clear_embedding: bool = False,
    allowed_mcp_key_ids: list[int] | None = None,
) -> tuple[KnowledgeBase, bool]:
    """更新知识库；返回 (base, embedding_changed)。"""
    base = get_base(db, kb_id)
    if name is not None:
        base.name = name.strip()
    if description is not None:
        base.description = description.strip()
    if scope is not None:
        base.scope = assert_scope_valid(scope)

    embedding_changed = False
    if clear_embedding:
        embedding_changed = bool(base.embedding_account_id or base.embedding_model or base.embedding_dimensions)
        base.embedding_account_id = None
        base.embedding_model = None
        base.embedding_dimensions = None
    elif embedding_account_id is not None or embedding_model is not None or embedding_dimensions is not None:
        next_account = embedding_account_id if embedding_account_id is not None else base.embedding_account_id
        next_model = embedding_model if embedding_model is not None else base.embedding_model
        next_dimensions = embedding_dimensions if embedding_dimensions is not None else base.embedding_dimensions
        account_id, model, dimensions = validate_embedding_binding(db, next_account, next_model, next_dimensions)
        before = base_signature(base)
        base.embedding_account_id = account_id
        base.embedding_model = model
        base.embedding_dimensions = dimensions
        embedding_changed = before != base_signature(base)

    if allowed_mcp_key_ids is not None:
        db.query(KnowledgeBaseKey).filter(KnowledgeBaseKey.kb_id == kb_id).delete(synchronize_session=False)
        for key_id in set(allowed_mcp_key_ids):
            db.add(KnowledgeBaseKey(kb_id=kb_id, mcp_key_id=int(key_id)))

    if embedding_changed:
        mark_documents_stale(db, kb_id)
    base.updated_at = utcnow()
    db.flush()
    db.refresh(base)
    return base, embedding_changed


def mark_documents_stale(db: Session, kb_id: str) -> None:
    documents = db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb_id)).all()
    for document in documents:
        document.vector_status = "stale"
        document.vector_error = None


def delete_base(db: Session, kb_id: str) -> None:
    from app.services import chroma_store
    from app.services.knowledge import fts

    base = get_base(db, kb_id)
    chunk_ids = list(db.scalars(select(KnowledgeChunk.id).where(KnowledgeChunk.kb_id == kb_id)).all())
    if chunk_ids:
        fts.delete_chunk_ids(db, chunk_ids)
    chroma_store.delete_collection(kb_id)
    db.delete(base)
    db.flush()


def allowed_keys(db: Session, kb_id: str) -> list[int]:
    return allowed_key_ids(db, kb_id)

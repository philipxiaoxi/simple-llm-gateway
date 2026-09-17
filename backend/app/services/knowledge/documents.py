from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.services import chroma_store
from app.services.knowledge import fts
from app.services.knowledge.bases import get_base
from app.services.knowledge.common import REEMBED_STATUSES
from app.services.knowledge.errors import KnowledgeError


def list_documents(db: Session, kb_id: str) -> list[KnowledgeDocument]:
    get_base(db, kb_id)
    return list(
        db.scalars(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.kb_id == kb_id)
            .order_by(KnowledgeDocument.created_at.desc())
        ).all()
    )


def get_document(db: Session, kb_id: str, document_id: str) -> KnowledgeDocument:
    document = db.get(KnowledgeDocument, document_id)
    if document is None or document.kb_id != kb_id:
        raise KnowledgeError("文档不存在", status_code=404, error_type="not_found")
    return document


def find_by_hash(db: Session, kb_id: str, content_hash: str) -> KnowledgeDocument | None:
    if not content_hash:
        return None
    return db.scalar(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.kb_id == kb_id, KnowledgeDocument.content_hash == content_hash)
        .order_by(KnowledgeDocument.created_at.desc())
        .limit(1)
    )


def list_document_ids(db: Session, kb_id: str, *, only_stale: bool = False) -> list[str]:
    stmt = select(KnowledgeDocument.id).where(KnowledgeDocument.kb_id == kb_id)
    if only_stale:
        stmt = stmt.where(KnowledgeDocument.vector_status.in_(REEMBED_STATUSES))
    return list(db.scalars(stmt.order_by(KnowledgeDocument.created_at)).all())


def delete_document(db: Session, kb_id: str, document_id: str) -> None:
    get_base(db, kb_id)
    document = get_document(db, kb_id, document_id)
    chunk_ids = list(db.scalars(select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == document_id)).all())
    if chunk_ids:
        fts.delete_chunk_ids(db, chunk_ids)
        chroma_store.delete_ids(kb_id, chunk_ids)
    db.delete(document)
    db.flush()
    base = db.get(KnowledgeBase, kb_id)
    if base is not None:
        base.updated_at = utcnow()

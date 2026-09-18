from __future__ import annotations

import contextlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_admin
from app.models import UpstreamAccount
from app.schemas import (
    KnowledgeBaseCreate,
    KnowledgeBaseOut,
    KnowledgeBaseUpdate,
    KnowledgeDocumentCreate,
    KnowledgeDocumentOut,
    KnowledgeEmbeddingAccountOut,
    KnowledgeReindexRequest,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.services import knowledge as knowledge_service
from app.services import knowledge_jobs
from app.services.key_models import is_account_available
from app.services.knowledge import Access, KnowledgeError
from app.services.model_caps import first_model_id, parse_model_records

router = APIRouter(
    prefix="/api/admin/mcp/knowledge",
    tags=["admin-mcp-knowledge"],
    dependencies=[Depends(get_current_admin)],
)

ADMIN = Access.for_admin()


def _http_error(error: KnowledgeError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"error": {"type": error.error_type, "message": error.message}},
    )


def _base_row(item: dict[str, Any]) -> KnowledgeBaseOut:
    return KnowledgeBaseOut(**item)


def _doc_out(doc) -> KnowledgeDocumentOut:
    return KnowledgeDocumentOut(
        id=doc.id,
        kb_id=doc.kb_id,
        source_name=doc.source_name,
        content_size=doc.content_size,
        chunk_count=doc.chunk_count,
        vector_status=doc.vector_status,
        vector_error=doc.vector_error,
        created_at=doc.created_at,
    )


@router.get("/embedding-accounts", response_model=list[KnowledgeEmbeddingAccountOut])
def list_embedding_accounts(db: Session = Depends(get_db)):
    rows = db.scalars(select(UpstreamAccount).order_by(UpstreamAccount.id)).all()
    items: list[KnowledgeEmbeddingAccountOut] = []
    for account in rows:
        models = [record.id for record in parse_model_records(account.models_json) if record.enabled]
        if not models and account.models_json:
            with contextlib.suppress(Exception):
                parsed = json.loads(account.models_json)
                if isinstance(parsed, list):
                    for entry in parsed:
                        if isinstance(entry, str) and entry.strip():
                            models.append(entry.strip())
                        elif isinstance(entry, dict) and entry.get("id"):
                            models.append(str(entry["id"]).strip())
        emb = [m for m in models if "embed" in m.lower()]
        other = [m for m in models if "embed" not in m.lower()]
        ordered = emb + other
        default_model = emb[0] if emb else (first_model_id(account.models_json) or (ordered[0] if ordered else None))
        items.append(
            KnowledgeEmbeddingAccountOut(
                id=account.id,
                name=account.name,
                provider=account.provider,
                source=account.source or "upstream",
                available=is_account_available(account),
                default_model=default_model or None,
                models=ordered[:300],
            )
        )
    return items


@router.get("/bases", response_model=list[KnowledgeBaseOut])
def list_bases(db: Session = Depends(get_db)):
    return [_base_row(item) for item in knowledge_service.list_bases(db, ADMIN)]


@router.post("/bases", response_model=KnowledgeBaseOut, status_code=201)
def create_base(payload: KnowledgeBaseCreate, db: Session = Depends(get_db)):
    try:
        base = knowledge_service.create_base(
            db,
            payload.name,
            payload.description,
            scope=payload.scope,
            embedding_account_id=payload.embedding_account_id,
            embedding_model=payload.embedding_model,
            embedding_dimensions=payload.embedding_dimensions,
            allowed_mcp_key_ids=payload.allowed_mcp_key_ids,
        )
    except KnowledgeError as error:
        raise _http_error(error) from error
    rows = knowledge_service.list_bases(db, ADMIN)
    match = next((row for row in rows if row["id"] == base.id), None)
    assert match is not None
    return _base_row(match)


@router.patch("/bases/{kb_id}", response_model=KnowledgeBaseOut)
def update_base(kb_id: str, payload: KnowledgeBaseUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True)
    clear_embedding = (
        "embedding_account_id" in data
        and data.get("embedding_account_id") is None
        and data.get("embedding_model") in (None, "")
    )
    try:
        _base, embedding_changed = knowledge_service.update_base(
            db,
            kb_id,
            name=data.get("name"),
            description=data.get("description"),
            scope=data.get("scope"),
            embedding_account_id=data.get("embedding_account_id"),
            embedding_model=data.get("embedding_model"),
            embedding_dimensions=data.get("embedding_dimensions"),
            clear_embedding=clear_embedding,
            allowed_mcp_key_ids=data.get("allowed_mcp_key_ids"),
        )
    except KnowledgeError as error:
        raise _http_error(error) from error
    result = next(row for row in knowledge_service.list_bases(db, ADMIN) if row["id"] == kb_id)
    if embedding_changed:
        result["embedding_changed"] = True
    return _base_row(result)


@router.post("/bases/{kb_id}/reindex")
def reindex_base(kb_id: str, payload: KnowledgeReindexRequest, db: Session = Depends(get_db)):
    """为知识库全部（或仅过期）文档排队重新向量化任务。"""
    try:
        knowledge_service.get_base(db, kb_id)
        created = knowledge_jobs.enqueue_reindex(db, kb_id, only_stale=payload.only_stale)
    except KnowledgeError as error:
        raise _http_error(error) from error
    return {"created": created}


@router.delete("/bases/{kb_id}", status_code=204)
def delete_base(kb_id: str, db: Session = Depends(get_db)):
    try:
        knowledge_service.delete_base(db, kb_id)
    except KnowledgeError as error:
        raise _http_error(error) from error


@router.get("/bases/{kb_id}/documents", response_model=list[KnowledgeDocumentOut])
def list_documents(kb_id: str, db: Session = Depends(get_db)):
    try:
        docs = knowledge_service.list_documents(db, kb_id)
    except KnowledgeError as error:
        raise _http_error(error) from error
    return [_doc_out(doc) for doc in docs]


@router.post("/bases/{kb_id}/documents", response_model=KnowledgeDocumentOut, status_code=201)
async def create_document_json(kb_id: str, payload: KnowledgeDocumentCreate, db: Session = Depends(get_db)):
    try:
        doc = await knowledge_service.add_document(
            db, kb_id, text=payload.text, source_name=payload.source_name or "paste.txt"
        )
    except KnowledgeError as error:
        raise _http_error(error) from error
    return _doc_out(doc)


@router.delete("/bases/{kb_id}/documents/{doc_id}", status_code=204)
def delete_document(kb_id: str, doc_id: str, db: Session = Depends(get_db)):
    try:
        knowledge_service.delete_document(db, kb_id, doc_id)
    except KnowledgeError as error:
        raise _http_error(error) from error


@router.post("/bases/{kb_id}/documents/{doc_id}/reembed", response_model=KnowledgeDocumentOut)
async def reembed_document(kb_id: str, doc_id: str, db: Session = Depends(get_db)):
    try:
        doc = await knowledge_service.reembed_document(db, kb_id, doc_id)
    except KnowledgeError as error:
        raise _http_error(error) from error
    return _doc_out(doc)


@router.post("/bases/{kb_id}/search", response_model=KnowledgeSearchResponse)
async def admin_search(kb_id: str, payload: KnowledgeSearchRequest, db: Session = Depends(get_db)):
    try:
        result = await knowledge_service.search(
            db,
            ADMIN,
            kb_id=kb_id,
            kb_ids=payload.kb_ids,
            query=payload.query,
            mode=payload.mode,
            top_k=payload.top_k,
        )
    except KnowledgeError as error:
        raise _http_error(error) from error
    return KnowledgeSearchResponse(**result)

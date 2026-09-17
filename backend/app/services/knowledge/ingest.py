from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.services import chroma_store
from app.services.chunking import split_text
from app.services.embedding import EmbeddingError, get_embedding_client_for_base
from app.services.knowledge import fts
from app.services.knowledge.bases import base_signature, get_base
from app.services.knowledge.common import emit, new_id
from app.services.knowledge.documents import find_by_hash, get_document
from app.services.knowledge.errors import KnowledgeError


@dataclass
class PrepareResult:
    document: KnowledgeDocument
    chunks: list[KnowledgeChunk]
    deduped: bool = False


def content_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def prepare_document(
    db: Session,
    kb_id: str,
    *,
    text: str,
    source_name: str = "paste.txt",
    on_progress=None,
    dedupe: bool = True,
) -> PrepareResult:
    """校验、分块、写入文档与全文索引；不做 commit，由调用方决定。"""
    await emit(on_progress, stage="validate", percent=5, message="校验文本…")
    base = get_base(db, kb_id)
    settings = get_settings()
    body = (text or "").strip()
    if not body:
        raise KnowledgeError("内容不能为空")
    size = len(body.encode("utf-8"))
    if size > settings.mcp_knowledge_max_bytes:
        raise KnowledgeError("内容超过大小上限", status_code=413, error_type="invalid_request")
    digest = content_digest(body)

    if dedupe:
        existing = find_by_hash(db, kb_id, digest)
        if existing is not None and existing.vector_status == "ready":
            await emit(
                on_progress,
                stage="chunk",
                percent=100,
                message="内容已存在，跳过重复入库",
                chunk_count=existing.chunk_count,
                content_size=existing.content_size,
            )
            return PrepareResult(document=existing, chunks=[], deduped=True)

    await emit(on_progress, stage="chunk", percent=12, message="文本分块中…")
    pieces = split_text(body, settings.mcp_chunk_size, settings.mcp_chunk_overlap)
    if not pieces:
        raise KnowledgeError("分块结果为空")
    await emit(
        on_progress,
        stage="chunk",
        percent=20,
        message=f"已分成 {len(pieces)} 块",
        chunk_count=len(pieces),
        content_size=size,
    )

    document = KnowledgeDocument(
        id=new_id(),
        kb_id=base.id,
        source_name=(source_name or "paste.txt").strip()[:256],
        content_size=size,
        content_hash=digest,
        chunk_count=len(pieces),
        vector_status="pending",
    )
    db.add(document)
    db.flush()

    await emit(on_progress, stage="save", percent=28, message="写入分块…", document_id=document.id)
    chunks: list[KnowledgeChunk] = []
    total_pieces = len(pieces)
    step = max(1, total_pieces // 10)
    for index, piece in enumerate(pieces):
        chunk = KnowledgeChunk(
            id=new_id(),
            document_id=document.id,
            kb_id=base.id,
            chunk_index=index,
            text=piece,
            source_name=document.source_name,
        )
        db.add(chunk)
        chunks.append(chunk)
        if total_pieces > 20 and (index + 1) % step == 0:
            percent = 28 + int(12 * (index + 1) / total_pieces)
            await emit(
                on_progress,
                stage="save",
                percent=min(percent, 40),
                message=f"写入分块 {index + 1}/{total_pieces}",
                current=index + 1,
                total=total_pieces,
            )
    db.flush()

    await emit(on_progress, stage="fts", percent=42, message="建立全文索引…")
    for index, chunk in enumerate(chunks):
        fts.insert_chunk(db, chunk)
        if total_pieces > 20 and (index + 1) % step == 0:
            percent = 42 + int(8 * (index + 1) / total_pieces)
            await emit(
                on_progress,
                stage="fts",
                percent=min(percent, 50),
                message=f"全文索引 {index + 1}/{total_pieces}",
                current=index + 1,
                total=total_pieces,
            )
    base.updated_at = utcnow()
    db.flush()
    return PrepareResult(document=document, chunks=chunks)


async def add_document(
    db: Session,
    kb_id: str,
    *,
    text: str,
    source_name: str = "paste.txt",
    on_progress=None,
) -> KnowledgeDocument:
    prepared = await prepare_document(db, kb_id, text=text, source_name=source_name, on_progress=on_progress)
    if prepared.deduped:
        return prepared.document
    await embed_document_chunks(db, prepared.document, prepared.chunks, on_progress=on_progress)
    db.flush()
    db.refresh(prepared.document)
    await emit(
        on_progress,
        stage="finish",
        percent=100,
        message="完成",
        document_id=prepared.document.id,
        vector_status=prepared.document.vector_status,
        vector_error=prepared.document.vector_error,
        chunk_count=prepared.document.chunk_count,
    )
    return prepared.document


async def embed_document_chunks(
    db: Session,
    document: KnowledgeDocument,
    chunks: list[KnowledgeChunk],
    on_progress=None,
) -> None:
    if not chunks:
        return
    try:
        await emit(
            on_progress,
            stage="embed",
            percent=52,
            message=f"向量化 0/{len(chunks)}…",
            current=0,
            total=len(chunks),
        )
        base = get_base(db, document.kb_id)
        client = get_embedding_client_for_base(db, base)
        signature = base_signature(base)

        async def on_batch(done: int, total: int, batch_index: int, total_batches: int) -> None:
            percent = 52 + int(38 * (done / max(total, 1)))
            await emit(
                on_progress,
                stage="embed",
                percent=min(percent, 90),
                message=f"向量化 {done}/{total}（批次 {batch_index}/{total_batches}）",
                current=done,
                total=total,
                batch_index=batch_index,
                total_batches=total_batches,
            )

        embeddings = await client.embed([chunk.text for chunk in chunks], on_batch=on_batch)
        await emit(on_progress, stage="store", percent=94, message="写入向量库…")
        replaced = chroma_store.upsert_chunks(
            document.kb_id,
            signature=signature,
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            embeddings=embeddings,
            metadatas=[
                {
                    "document_id": chunk.document_id,
                    "kb_id": chunk.kb_id,
                    "source_name": chunk.source_name,
                    "chunk_index": chunk.chunk_index,
                }
                for chunk in chunks
            ],
        )
        document.vector_status = "ready"
        document.vector_error = None
        if replaced is not None:
            await emit(
                on_progress,
                stage="store",
                percent=98,
                message=f"向量配置已变更，旧向量（{replaced}）已清空并按新配置重建",
            )
        await emit(on_progress, stage="store", percent=98, message="向量写入完成")
    except EmbeddingError as error:
        document.vector_status = "failed"
        document.vector_error = error.message[:500]
        await emit(
            on_progress,
            stage="error",
            percent=98,
            message=f"向量化失败：{error.message[:200]}",
            vector_status="failed",
            vector_error=document.vector_error,
        )
    except Exception as error:  # noqa: BLE001
        document.vector_status = "failed"
        document.vector_error = str(error)[:500]
        await emit(
            on_progress,
            stage="error",
            percent=98,
            message=f"向量写入失败：{str(error)[:200]}",
            vector_status="failed",
            vector_error=document.vector_error,
        )


def document_chunks(db: Session, document_id: str) -> list[KnowledgeChunk]:
    return list(
        db.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document_id)
            .order_by(KnowledgeChunk.chunk_index)
        ).all()
    )


async def reembed_document(
    db: Session,
    kb_id: str,
    document_id: str,
    on_progress=None,
) -> KnowledgeDocument:
    get_base(db, kb_id)
    document = get_document(db, kb_id, document_id)
    chunks = document_chunks(db, document_id)
    await emit(
        on_progress,
        stage="embed",
        percent=10,
        message=f"准备重新向量化 {len(chunks)} 块…",
        chunk_count=len(chunks),
        document_id=document.id,
    )
    await embed_document_chunks(db, document, chunks, on_progress=on_progress)
    db.flush()
    db.refresh(document)
    await emit(
        on_progress,
        stage="finish",
        percent=100,
        message="完成",
        document_id=document.id,
        vector_status=document.vector_status,
        vector_error=document.vector_error,
        chunk_count=document.chunk_count,
    )
    return document


def assert_base_ready_for_embedding(db: Session, base: KnowledgeBase) -> None:
    """确保知识库配置可生成向量（提前给出可读错误）。"""
    get_embedding_client_for_base(db, base)

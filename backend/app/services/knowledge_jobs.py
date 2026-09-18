from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.db import get_session_factory
from app.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument, KnowledgeIngestJob
from app.services import chroma_store
from app.services import knowledge as knowledge_service
from app.services.embedding import EmbeddingError, get_embedding_client_for_base
from app.services.knowledge.bases import base_signature
from app.services.knowledge.errors import JobCanceled, KnowledgeError

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "canceled"})
ACTIVE_STATUSES = frozenset({"queued", "running"})

_worker_tasks: list[asyncio.Task[None]] = []


def source_path(job_id: int) -> Path:
    return get_settings().resolved_knowledge_jobs_path / f"{job_id}.txt"


def write_source(job_id: int, text: str) -> None:
    source_path(job_id).write_text(text, encoding="utf-8")


def read_source(job_id: int) -> str:
    path = source_path(job_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def delete_source(job_id: int) -> None:
    with contextlib.suppress(FileNotFoundError):
        source_path(job_id).unlink()


def create_job(
    db: Session,
    *,
    kb_id: str,
    kind: str = "ingest",
    source_name: str = "paste.txt",
    text: str | None = None,
    document_id: str | None = None,
) -> KnowledgeIngestJob:
    settings = get_settings()
    job = KnowledgeIngestJob(
        kb_id=kb_id,
        kind=kind,
        source_name=(source_name or "paste.txt").strip()[:256],
        status="queued",
        stage="queued",
        percent=0,
        message="排队中",
        max_attempts=settings.mcp_knowledge_job_max_attempts,
        document_id=document_id,
    )
    db.add(job)
    db.flush()
    if text is not None:
        job.content_size = len(text.encode("utf-8"))
        write_source(job.id, text)
    db.flush()
    db.refresh(job)
    return job


def enqueue_reindex(db: Session, kb_id: str, *, only_stale: bool = False) -> int:
    """为知识库的全部（或仅过期）文档排队重新向量化任务。"""
    document_ids = knowledge_service.list_document_ids(db, kb_id, only_stale=only_stale)
    documents = {
        document.id: document
        for document in db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.id.in_(document_ids))).all()
    }
    created = 0
    for document_id in document_ids:
        document = documents.get(document_id)
        if document is None:
            continue
        create_job(
            db,
            kb_id=kb_id,
            kind="reembed",
            source_name=document.source_name,
            document_id=document.id,
        )
        created += 1
    return created


def reconcile_stuck_jobs(db: Session) -> int:
    """进程重启后，把仍在 running 的任务复位为可重试的 failed。"""
    rows = db.scalars(select(KnowledgeIngestJob).where(KnowledgeIngestJob.status == "running")).all()
    for job in rows:
        job.status = "failed"
        job.stage = "error"
        job.message = "服务重启，任务已中断"
        job.error_message = "服务重启，任务已中断，可点击重试"
        job.finished_at = utcnow()
        job.updated_at = utcnow()
    if rows:
        db.commit()
    return len(rows)


def _claim_next_job() -> int | None:
    session = get_session_factory()()
    try:
        job_id = session.scalar(
            select(KnowledgeIngestJob.id)
            .where(KnowledgeIngestJob.status == "queued")
            .order_by(KnowledgeIngestJob.id)
            .limit(1)
        )
        if job_id is None:
            return None
        result = session.execute(
            update(KnowledgeIngestJob)
            .where(KnowledgeIngestJob.id == job_id, KnowledgeIngestJob.status == "queued")
            .values(
                status="running",
                stage="validate",
                percent=1,
                message="开始处理",
                attempts=KnowledgeIngestJob.attempts + 1,
                started_at=utcnow(),
                finished_at=None,
                error_message=None,
                updated_at=utcnow(),
            )
        )
        session.commit()
        return job_id if result.rowcount else None
    finally:
        session.close()


class _ProgressWriter:
    """把进度写回任务行；运行期不持有长事务，避免与向量写入相互阻塞。"""

    def __init__(self, job_id: int, min_interval: float = 0.25) -> None:
        self.job_id = job_id
        self.min_interval = min_interval
        self._last_at = 0.0
        self._last_percent = -1
        self._last_stage = ""
        self.canceled = False

    async def __call__(self, payload: dict[str, Any]) -> None:
        stage = str(payload.get("stage") or "")
        percent = int(payload.get("percent", 0) or 0)
        now = time.monotonic()
        # 阶段切换与终态必须落库，同一阶段内按时间/百分比节流
        force = stage in {"done", "error", "finish"} or stage != self._last_stage
        if not force and percent == self._last_percent:
            return
        if not force and now - self._last_at < self.min_interval:
            return
        self._last_at = now
        self._last_percent = percent
        self._last_stage = stage
        self.persist(payload)

    def persist(self, payload: dict[str, Any]) -> None:
        session = get_session_factory()()
        try:
            job = session.get(KnowledgeIngestJob, self.job_id)
            if job is None:
                return
            if job.status == "canceled":
                self.canceled = True
                return
            job.stage = str(payload.get("stage") or job.stage)[:16]
            job.percent = max(0, min(100, int(payload.get("percent", job.percent) or 0)))
            message = payload.get("message")
            if message:
                job.message = str(message)[:256]
            if payload.get("chunk_count") is not None:
                job.chunk_count = int(payload["chunk_count"])
            if payload.get("total") is not None:
                job.total_chunks = int(payload["total"])
            if payload.get("current") is not None:
                job.processed_chunks = int(payload["current"])
            if payload.get("document_id"):
                job.document_id = str(payload["document_id"])
            job.updated_at = utcnow()
            session.commit()
        finally:
            session.close()

    def is_canceled(self) -> bool:
        session = get_session_factory()()
        try:
            status = session.scalar(select(KnowledgeIngestJob.status).where(KnowledgeIngestJob.id == self.job_id))
            return status == "canceled"
        finally:
            session.close()

    def fail(self, message: str, *, stage: str = "error") -> None:
        self._finish("failed", message=message, stage=stage, error=message)

    def succeed(self, document_id: str | None) -> None:
        self._finish("succeeded", message="完成", stage="done", document_id=document_id)

    def canceled_out(self) -> None:
        self._finish("canceled", message="已取消", stage="canceled")

    def _finish(
        self,
        status: str,
        *,
        message: str,
        stage: str,
        error: str | None = None,
        document_id: str | None = None,
    ) -> None:
        session = get_session_factory()()
        try:
            job = session.get(KnowledgeIngestJob, self.job_id)
            if job is None:
                return
            if status == "succeeded" and job.status == "canceled":
                return
            job.status = status
            job.stage = stage
            job.message = message[:256]
            job.error_message = error[:2000] if error else None
            if status == "succeeded":
                job.percent = 100
                job.processed_chunks = job.total_chunks or job.processed_chunks
            if document_id:
                job.document_id = document_id
            job.finished_at = utcnow()
            job.updated_at = utcnow()
            session.commit()
        finally:
            session.close()


def _load_base(db: Session, kb_id: str) -> KnowledgeBase:
    base = db.get(KnowledgeBase, kb_id)
    if base is None:
        raise KnowledgeError("知识库不存在", status_code=404, error_type="not_found")
    return base


def _chunk_rows_for(document_id: str) -> list[tuple[str, str, int, str]]:
    session = get_session_factory()()
    try:
        rows = session.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document_id)
            .order_by(KnowledgeChunk.chunk_index)
        ).all()
        return [(row.id, row.text, row.chunk_index, row.source_name) for row in rows]
    finally:
        session.close()


def _set_document_vector_status(document_id: str, status: str, error: str | None) -> None:
    session = get_session_factory()()
    try:
        document = session.get(KnowledgeDocument, document_id)
        if document is not None:
            document.vector_status = status
            document.vector_error = error
        session.commit()
    finally:
        session.close()


async def _embed_document(kb_id: str, document_id: str, writer: _ProgressWriter) -> None:
    chunk_rows = _chunk_rows_for(document_id)
    total = len(chunk_rows)
    if total == 0:
        raise KnowledgeError("文档没有可向量化的分块")

    session = get_session_factory()()
    try:
        base = _load_base(session, kb_id)
        client = get_embedding_client_for_base(session, base)
        signature = base_signature(base)
    finally:
        session.close()

    _set_document_vector_status(document_id, "pending", None)
    await writer({"stage": "embed", "percent": 52, "message": f"向量化 0/{total}…", "current": 0, "total": total})

    async def on_batch(done: int, batch_total: int, batch_index: int, total_batches: int) -> None:
        if writer.is_canceled():
            raise JobCanceled()
        percent = 52 + int(38 * (done / max(batch_total, 1)))
        await writer(
            {
                "stage": "embed",
                "percent": min(percent, 90),
                "message": f"向量化 {done}/{batch_total}（批次 {batch_index}/{total_batches}）",
                "current": done,
                "total": batch_total,
            }
        )

    texts = [row[1] for row in chunk_rows]
    vector_status = "ready"
    vector_error: str | None = None
    try:
        embeddings = await client.embed(texts, on_batch=on_batch)
        if writer.is_canceled():
            raise JobCanceled()
        await writer({"stage": "store", "percent": 94, "message": "写入向量库…"})
        replaced = chroma_store.upsert_chunks(
            kb_id,
            signature=signature,
            ids=[row[0] for row in chunk_rows],
            documents=texts,
            embeddings=embeddings,
            metadatas=[
                {
                    "document_id": document_id,
                    "kb_id": kb_id,
                    "source_name": row[3],
                    "chunk_index": row[2],
                }
                for row in chunk_rows
            ],
        )
        if replaced is not None:
            await writer(
                {
                    "stage": "store",
                    "percent": 98,
                    "message": f"向量配置已变更，旧向量（{replaced}）已清空并按新配置重建",
                }
            )
        await writer({"stage": "store", "percent": 98, "message": "向量写入完成"})
    except JobCanceled:
        _set_document_vector_status(document_id, "pending", None)
        raise
    except EmbeddingError as error:
        vector_status = "failed"
        vector_error = error.message[:500]
    except Exception as error:  # noqa: BLE001
        vector_status = "failed"
        vector_error = str(error)[:500]

    _set_document_vector_status(document_id, vector_status, vector_error)
    if vector_status == "failed":
        raise KnowledgeError(f"向量化失败：{vector_error}", status_code=502, error_type="vector_unavailable")


async def _process_job(job_id: int) -> None:
    session = get_session_factory()()
    try:
        job = session.get(KnowledgeIngestJob, job_id)
        if job is None:
            return
        kb_id = job.kb_id
        document_id = job.document_id
        source_name = job.source_name
    finally:
        session.close()

    writer = _ProgressWriter(job_id)
    try:
        # 已存在文档（向量化阶段失败后重试，或 reembed 任务）：只重跑向量化
        if not document_id:
            text = read_source(job_id)
            if not text:
                raise KnowledgeError("任务原文缺失，无法重试，请重新提交")
            buffer: list[dict[str, Any]] = []

            async def buffered(payload: dict[str, Any]) -> None:
                buffer.append(payload)

            session = get_session_factory()()
            try:
                prepared = await knowledge_service.prepare_document(
                    session,
                    kb_id,
                    text=text,
                    source_name=source_name,
                    on_progress=buffered,
                )
                document_id = prepared.document.id
                deduped = prepared.deduped
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
            for payload in buffer:
                writer.persist(payload)
            if deduped:
                writer.succeed(document_id)
                return

        await writer({"stage": "save", "percent": 48, "message": "分块与全文索引完成", "document_id": document_id})
        await _embed_document(kb_id, document_id, writer)
        writer.succeed(document_id)
    except JobCanceled:
        writer.canceled_out()
    except KnowledgeError as error:
        writer.fail(error.message)
    except Exception as error:  # noqa: BLE001
        writer.fail(f"任务失败：{str(error)[:400]}")


async def _worker_loop() -> None:
    while True:
        try:
            job_id = _claim_next_job()
            if job_id is None:
                await asyncio.sleep(1.0)
                continue
            await _process_job(job_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            print(f"[knowledge-jobs] worker error: {error}")
            await asyncio.sleep(1.0)


def start_knowledge_job_workers() -> list[asyncio.Task[None]]:
    settings = get_settings()
    concurrency = max(1, min(int(settings.mcp_knowledge_job_concurrency or 1), 4))
    global _worker_tasks
    if _worker_tasks:
        return _worker_tasks
    _worker_tasks = [asyncio.create_task(_worker_loop()) for _ in range(concurrency)]
    return _worker_tasks


def reset_knowledge_job_workers() -> None:
    global _worker_tasks
    _worker_tasks = []


def job_counts(db: Session, *, kb_id: str | None = None) -> dict[str, int]:
    stmt = select(KnowledgeIngestJob.status, func.count()).group_by(KnowledgeIngestJob.status)
    if kb_id:
        stmt = stmt.where(KnowledgeIngestJob.kb_id == kb_id)
    return {status: int(total) for status, total in db.execute(stmt).all()}

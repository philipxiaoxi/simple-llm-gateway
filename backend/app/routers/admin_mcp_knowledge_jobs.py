from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_admin
from app.models import KnowledgeBase, KnowledgeDocument, KnowledgeIngestJob
from app.schemas import (
    KnowledgeJobCreate,
    KnowledgeJobListOut,
    KnowledgeJobOut,
    KnowledgeReembedJobCreate,
)
from app.services import knowledge_jobs
from app.services.knowledge import KnowledgeError

router = APIRouter(
    prefix="/api/admin/mcp/knowledge/jobs",
    tags=["admin-mcp-knowledge-jobs"],
    dependencies=[Depends(get_current_admin)],
)

VALID_STATUSES = {"queued", "running", "succeeded", "failed", "canceled"}


def _http_error(error: KnowledgeError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"error": {"type": error.error_type, "message": error.message}},
    )


def _kb_name(db: Session, kb_id: str) -> str | None:
    base = db.get(KnowledgeBase, kb_id)
    return base.name if base else None


def _out(db: Session, job: KnowledgeIngestJob) -> KnowledgeJobOut:
    return KnowledgeJobOut(
        id=job.id,
        kb_id=job.kb_id,
        kb_name=_kb_name(db, job.kb_id),
        kind=job.kind,
        source_name=job.source_name,
        content_size=job.content_size,
        chunk_count=job.chunk_count,
        status=job.status,
        stage=job.stage,
        percent=job.percent,
        message=job.message,
        processed_chunks=job.processed_chunks,
        total_chunks=job.total_chunks,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        document_id=job.document_id,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _require_base(db: Session, kb_id: str) -> KnowledgeBase:
    base = db.get(KnowledgeBase, kb_id)
    if base is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"type": "not_found", "message": "知识库不存在"}},
        )
    return base


@router.get("", response_model=KnowledgeJobListOut)
def list_jobs(
    db: Session = Depends(get_db),
    status: str | None = Query(default=None),
    kb_id: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    filters = []
    if status:
        statuses = [item.strip() for item in status.split(",") if item.strip()]
        if statuses:
            filters.append(KnowledgeIngestJob.status.in_(statuses))
    if kb_id:
        filters.append(KnowledgeIngestJob.kb_id == kb_id)
    if kind:
        filters.append(KnowledgeIngestJob.kind == kind)
    if q:
        filters.append(KnowledgeIngestJob.source_name.contains(q.strip()))

    total = db.scalar(select(func.count()).select_from(KnowledgeIngestJob).where(*filters)) or 0
    rows = db.scalars(
        select(KnowledgeIngestJob)
        .where(*filters)
        .order_by(KnowledgeIngestJob.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    counts = knowledge_jobs.job_counts(db, kb_id=kb_id or None)
    return KnowledgeJobListOut(
        items=[_out(db, job) for job in rows],
        total=int(total),
        counts=counts,
    )


@router.get("/{job_id}", response_model=KnowledgeJobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(KnowledgeIngestJob, job_id)
    if job is None:
        raise HTTPException(404, detail={"error": {"type": "not_found", "message": "任务不存在"}})
    return _out(db, job)


@router.post("", response_model=KnowledgeJobOut, status_code=201)
def create_text_job(payload: KnowledgeJobCreate, db: Session = Depends(get_db)):
    _require_base(db, payload.kb_id)
    try:
        job = knowledge_jobs.create_job(
            db,
            kb_id=payload.kb_id,
            kind="ingest",
            source_name=payload.source_name or "paste.txt",
            text=payload.text,
        )
    except KnowledgeError as error:
        raise _http_error(error) from error
    return _out(db, job)


@router.post("/upload", response_model=KnowledgeJobOut, status_code=201)
async def create_file_job(
    db: Session = Depends(get_db),
    kb_id: str = Form(...),
    file: UploadFile = File(...),
):
    _require_base(db, kb_id)
    settings = get_settings()
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail={"error": {"type": "invalid_request", "message": "文件为空"}})
    if len(raw) > settings.mcp_knowledge_max_bytes:
        raise HTTPException(
            413,
            detail={
                "error": {
                    "type": "invalid_request",
                    "message": f"文件超过上限 {settings.mcp_knowledge_max_bytes} 字节",
                }
            },
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    try:
        job = knowledge_jobs.create_job(
            db,
            kb_id=kb_id,
            kind="ingest",
            source_name=file.filename or "upload.txt",
            text=text,
        )
    except KnowledgeError as error:
        raise _http_error(error) from error
    return _out(db, job)


@router.post("/reembed", response_model=KnowledgeJobOut, status_code=201)
def create_reembed_job(payload: KnowledgeReembedJobCreate, db: Session = Depends(get_db)):
    _require_base(db, payload.kb_id)
    document = db.get(KnowledgeDocument, payload.document_id)
    if document is None or document.kb_id != payload.kb_id:
        raise HTTPException(404, detail={"error": {"type": "not_found", "message": "文档不存在"}})
    job = knowledge_jobs.create_job(
        db,
        kb_id=payload.kb_id,
        kind="reembed",
        source_name=document.source_name,
        document_id=document.id,
    )
    return _out(db, job)


@router.post("/{job_id}/retry", response_model=KnowledgeJobOut)
def retry_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(KnowledgeIngestJob, job_id)
    if job is None:
        raise HTTPException(404, detail={"error": {"type": "not_found", "message": "任务不存在"}})
    if job.status in {"queued", "running"}:
        raise HTTPException(409, detail={"error": {"type": "invalid_request", "message": "任务进行中，无需重试"}})
    if job.attempts >= job.max_attempts:
        raise HTTPException(
            409,
            detail={
                "error": {
                    "type": "invalid_request",
                    "message": f"已重试 {job.attempts} 次，达到上限 {job.max_attempts}",
                }
            },
        )
    if job.kind == "ingest" and not job.document_id and not knowledge_jobs.read_source(job.id):
        raise HTTPException(
            409,
            detail={"error": {"type": "invalid_request", "message": "任务原文已丢失，请重新提交"}},
        )
    job.status = "queued"
    job.stage = "queued"
    job.percent = 0
    job.message = "已重新排队"
    job.error_message = None
    job.processed_chunks = 0
    job.finished_at = None
    job.updated_at = utcnow()
    db.flush()
    db.refresh(job)
    return _out(db, job)


@router.post("/{job_id}/cancel", response_model=KnowledgeJobOut)
def cancel_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(KnowledgeIngestJob, job_id)
    if job is None:
        raise HTTPException(404, detail={"error": {"type": "not_found", "message": "任务不存在"}})
    if job.status == "running":
        raise HTTPException(
            409,
            detail={"error": {"type": "invalid_request", "message": "任务执行中，暂不支持取消，请等待完成"}},
        )
    if job.status != "queued":
        raise HTTPException(409, detail={"error": {"type": "invalid_request", "message": "任务已结束"}})
    job.status = "canceled"
    job.stage = "canceled"
    job.message = "已取消"
    job.finished_at = utcnow()
    job.updated_at = utcnow()
    db.flush()
    db.refresh(job)
    return _out(db, job)


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(KnowledgeIngestJob, job_id)
    if job is None:
        raise HTTPException(404, detail={"error": {"type": "not_found", "message": "任务不存在"}})
    if job.status == "running":
        raise HTTPException(
            409,
            detail={"error": {"type": "invalid_request", "message": "任务执行中，不能删除"}},
        )
    knowledge_jobs.delete_source(job.id)
    db.delete(job)
    db.flush()

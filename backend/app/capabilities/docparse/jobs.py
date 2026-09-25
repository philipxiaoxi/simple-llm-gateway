from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.models import DocParseJob, KnowledgeBase
from app.services import knowledge_jobs
from app.services.knowledge.text_files import clean_source_name

from .convert import RESULT_CHAR_CAP, assert_supported, convert_bytes, markdown_source_name
from .errors import DocParseError
from .storage import markdown_path, purge_job_files, read_markdown, write_markdown, write_source


def _new_id() -> str:
    from uuid import uuid4

    return str(uuid4())


def create_job(
    db: Session,
    *,
    filename: str,
    raw: bytes,
    created_by: str = "admin",
    mcp_key_id: int | None = None,
) -> DocParseJob:
    ext = assert_supported(filename, raw)
    job = DocParseJob(
        id=_new_id(),
        mcp_key_id=mcp_key_id,
        created_by=created_by,
        source_name=clean_source_name(filename, fallback=f"upload{ext}"),
        source_ext=ext,
        source_size=len(raw),
        status="queued",
        stage="stored",
        percent=10,
        message="已接收，等待转换",
    )
    db.add(job)
    db.flush()
    write_source(job.id, ext, raw)
    db.flush()
    return job


def run_job(db: Session, job: DocParseJob, raw: bytes) -> DocParseJob:
    if job.status == "cancelled":
        return job
    job.status = "running"
    job.stage = "converting"
    job.percent = 40
    job.message = "转换中"
    job.started_at = utcnow()
    job.attempts += 1
    job.updated_at = utcnow()
    db.flush()
    try:
        result = convert_bytes(job.source_name, raw)
    except DocParseError as error:
        job.status = "failed"
        job.stage = "done"
        job.percent = 100
        job.error_message = error.message[:2000]
        job.message = "转换失败"
        job.finished_at = utcnow()
        job.updated_at = utcnow()
        db.flush()
        raise
    if job.status == "cancelled":
        return job
    job.stage = "writing"
    job.percent = 80
    job.message = "写入结果"
    db.flush()
    write_markdown(job.id, result.markdown)
    job.markdown_bytes = len(result.markdown.encode("utf-8"))
    job.page_count = result.page_count
    job.warnings_json = json.dumps(result.warnings, ensure_ascii=False)
    job.status = "succeeded"
    job.stage = "done"
    job.percent = 100
    job.message = "转换完成"
    job.error_message = None
    job.finished_at = utcnow()
    job.updated_at = utcnow()
    db.flush()
    return job


def get_owned_job(db: Session, job_id: str, mcp_key_id: int | None) -> DocParseJob:
    job = db.get(DocParseJob, job_id)
    if job is None or (mcp_key_id is not None and job.mcp_key_id != mcp_key_id):
        raise DocParseError("任务不存在", status_code=404, error_type="not_found")
    return job


def job_payload(job: DocParseJob, *, include_markdown: bool = False) -> dict:
    warnings = []
    try:
        parsed = json.loads(job.warnings_json or "[]")
        if isinstance(parsed, list):
            warnings = [str(item) for item in parsed]
    except json.JSONDecodeError:
        warnings = []
    payload = {
        "job_id": job.id,
        "source_name": job.source_name,
        "source_ext": job.source_ext,
        "source_size": job.source_size,
        "status": job.status,
        "stage": job.stage,
        "percent": job.percent,
        "message": job.message,
        "warnings": warnings,
        "error_message": job.error_message,
        "markdown_bytes": job.markdown_bytes,
        "page_count": job.page_count,
        "ingest_job_id": job.ingest_job_id,
        "download_ready": job.status == "succeeded" and markdown_path(job.id).is_file(),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
    if include_markdown:
        text = read_markdown(job.id) if payload["download_ready"] else ""
        truncated = len(text) > RESULT_CHAR_CAP
        payload["markdown"] = text[:RESULT_CHAR_CAP]
        payload["truncated"] = truncated
    return payload


def cancel_job(db: Session, job: DocParseJob) -> DocParseJob:
    if job.status not in {"queued", "running"}:
        raise DocParseError("只有排队中或转换中的任务可以取消")
    job.status = "cancelled"
    job.stage = "done"
    job.percent = 100
    job.message = "已取消"
    job.finished_at = utcnow()
    job.updated_at = utcnow()
    db.flush()
    return job


def retry_job(db: Session, job: DocParseJob) -> DocParseJob:
    if job.status != "failed":
        raise DocParseError("只有失败任务可以重试")
    job.status = "queued"
    job.stage = "stored"
    job.percent = 10
    job.message = "已重新排队"
    job.error_message = None
    job.finished_at = None
    job.updated_at = utcnow()
    db.flush()
    return job


def ingest_job(db: Session, job: DocParseJob, kb_id: str) -> DocParseJob:
    if job.status != "succeeded" or not markdown_path(job.id).is_file():
        raise DocParseError("只有转换成功且结果仍在的任务可以送入知识库")
    base = db.get(KnowledgeBase, kb_id)
    if base is None:
        raise DocParseError("知识库不存在", status_code=404, error_type="not_found")
    text = read_markdown(job.id)
    created = knowledge_jobs.create_job(
        db,
        kb_id=kb_id,
        kind="ingest",
        source_name=markdown_source_name(job.source_name),
        text=text,
    )
    job.ingest_job_id = created.id
    job.updated_at = utcnow()
    db.flush()
    return job


def delete_job(db: Session, job: DocParseJob) -> None:
    purge_job_files(job.id)
    db.delete(job)
    db.flush()


def list_jobs(
    db: Session,
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    q: str | None = None,
) -> tuple[list[DocParseJob], int, dict[str, int]]:
    filters = []
    if status:
        statuses = [item.strip() for item in status.split(",") if item.strip()]
        if statuses:
            filters.append(DocParseJob.status.in_(statuses))
    if q and q.strip():
        filters.append(DocParseJob.source_name.contains(q.strip()))
    total = db.scalar(select(func.count()).select_from(DocParseJob).where(*filters)) or 0
    rows = list(
        db.scalars(
            select(DocParseJob).where(*filters).order_by(DocParseJob.created_at.desc()).offset(offset).limit(limit)
        ).all()
    )
    counts = {
        key: int(db.scalar(select(func.count()).select_from(DocParseJob).where(DocParseJob.status == key)) or 0)
        for key in ("queued", "running", "succeeded", "failed", "cancelled")
    }
    return rows, int(total), counts


def cleanup_expired(db: Session) -> int:
    days = max(0, int(get_settings().doc_parse_retention_days))
    if not days:
        return 0
    cutoff = utcnow() - timedelta(days=days)
    rows = db.scalars(
        select(DocParseJob).where(
            DocParseJob.purged == 0,
            DocParseJob.finished_at.is_not(None),
            DocParseJob.finished_at < cutoff,
            DocParseJob.status.in_(("succeeded", "failed", "cancelled")),
        )
    ).all()
    removed = 0
    for job in rows:
        purge_job_files(job.id)
        job.purged = 1
        removed += 1
    if removed:
        db.flush()
    return removed

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_admin
from app.models import ContentAuditFinding
from app.schemas import (
    ContentAuditFindingListOut,
    ContentAuditFindingOut,
    ContentAuditLexiconSyncOut,
    ContentAuditSummaryOut,
)
from app.services import content_audit
from app.services.jobs import JOB_CONTENT_AUDIT, get_job_runtime

router = APIRouter(
    prefix="/api/admin/content-audit",
    tags=["admin-content-audit"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("/findings", response_model=ContentAuditFindingListOut)
def list_findings(
    category: str | None = None,
    lexicon_category: str | None = None,
    severity: str | None = None,
    api_key_id: int | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ContentAuditFindingListOut:
    filters = select(ContentAuditFinding)
    if category:
        filters = filters.where(ContentAuditFinding.category == category)
    if lexicon_category:
        filters = filters.where(ContentAuditFinding.lexicon_category == lexicon_category)
    if severity:
        filters = filters.where(ContentAuditFinding.severity == severity)
    if api_key_id is not None:
        filters = filters.where(ContentAuditFinding.api_key_id == api_key_id)
    total = db.scalar(select(func.count()).select_from(filters.subquery())) or 0
    offset = (page - 1) * page_size
    rows = db.scalars(
        filters.order_by(ContentAuditFinding.created_at.desc(), ContentAuditFinding.id.desc())
        .offset(offset)
        .limit(page_size)
    ).all()
    items = [
        ContentAuditFindingOut(
            id=row.id,
            log_id=row.log_id,
            message_seq=row.message_seq,
            category=row.category,
            lexicon_category=row.lexicon_category,
            rule_key=row.rule_key,
            severity=row.severity,
            excerpt=content_audit.mask_excerpt_for_list(row.excerpt, row.category, row.rule_key),
            start_offset=row.start_offset,
            end_offset=row.end_offset,
            api_key_id=row.api_key_id,
            api_key_name=row.api_key_name,
            account_name=row.account_name,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return ContentAuditFindingListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/summary", response_model=ContentAuditSummaryOut)
def get_summary(db: Session = Depends(get_db)) -> ContentAuditSummaryOut:
    stats = content_audit.progress_stats(db)
    scan = content_audit.scan_status()
    state = get_job_runtime(JOB_CONTENT_AUDIT)
    extra = state.extra or {}
    if scan["running"]:
        status = "paused" if scan["paused"] else "running"
    elif extra.get("lexicon_ok") is False and state.last_ok:
        status = "partial"
    elif state.last_ok is False:
        status = "failed"
    elif state.last_ok:
        status = "ok"
    else:
        status = "idle"
    error_message = scan.get("error") or state.error_message or extra.get("error_message")
    lexicon = content_audit.lexicon_info()
    return ContentAuditSummaryOut(
        running=scan["running"],
        status=status,
        paused=scan["paused"],
        scanned_in_run=scan["scanned"],
        total_in_run=scan["total"],
        last_finished_at=state.last_finished_at,
        last_message=state.last_message,
        error_message=error_message,
        scanned_count=stats["scanned_count"],
        total_logs=stats["total_logs"],
        finding_count=stats["finding_count"],
        remaining=stats["remaining"],
        processed=extra.get("processed"),
        new_findings=extra.get("new_findings"),
        lexicon_ok=extra.get("lexicon_ok"),
        lexicon_updated_at=content_audit.lexicon_updated_at(),
        lexicon_word_count=lexicon["word_count"],
        by_category=stats["by_category"],
        lexicon_categories=lexicon["categories"],
    )


@router.post("/scan/start")
def start_scan() -> dict[str, Any]:
    return content_audit.start_scan()


@router.post("/scan/stop")
def stop_scan() -> dict[str, Any]:
    return content_audit.stop_scan()


@router.post("/scan/pause")
def pause_scan() -> dict[str, Any]:
    return content_audit.pause_scan()


@router.post("/scan/resume")
def resume_scan() -> dict[str, Any]:
    return content_audit.resume_scan()


@router.post("/lexicon/sync", response_model=ContentAuditLexiconSyncOut)
def sync_lexicon() -> ContentAuditLexiconSyncOut:
    try:
        result = content_audit.sync_lexicon()
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return ContentAuditLexiconSyncOut.model_validate(result)

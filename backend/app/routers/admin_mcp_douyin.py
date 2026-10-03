from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.capabilities.douyin import jobs, provider_config
from app.capabilities.douyin.errors import DouyinError
from app.db import get_db
from app.deps import get_current_admin

router = APIRouter(
    prefix="/api/admin/mcp/douyin",
    tags=["admin-mcp-douyin"],
    dependencies=[Depends(get_current_admin)],
)


def _http(error: DouyinError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"error": {"type": error.error_type, "message": error.message}},
    )


@router.get("/provider")
def get_provider_status(db: Session = Depends(get_db)):
    return provider_config.status(db)


@router.put("/provider")
def set_provider(payload: dict[str, Any] | None = None, db: Session = Depends(get_db)):
    body = payload or {}
    try:
        provider_config.set_config(db, base_url=body.get("base_url"), api_key=body.get("api_key"))
    except DouyinError as error:
        raise _http(error) from error
    db.commit()
    return provider_config.status(db)


@router.delete("/provider")
def clear_provider(db: Session = Depends(get_db)):
    provider_config.clear_config(db)
    db.commit()
    return provider_config.status(db)


@router.get("/jobs")
def admin_list(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    rows, total = jobs.list_jobs(db, limit=limit, offset=offset, q=q, status=status)
    return {"items": [jobs.job_payload(job) for job in rows], "total": total}


@router.post("/jobs", status_code=202)
def admin_create(
    background: BackgroundTasks,
    payload: dict[str, Any] | None = None,
    db: Session = Depends(get_db),
):
    body = payload or {}
    raw = str(body.get("url") or body.get("share_text") or "").strip()
    if not raw:
        raise HTTPException(
            status_code=400,
            detail={"error": {"type": "invalid_request", "message": "url 或 share_text 至少填写一个"}},
        )
    rehost_value = body.get("rehost", True)
    rehost = True if rehost_value is None else bool(rehost_value)
    try:
        job = jobs.create_job(db, raw_input=raw, rehost=rehost, created_by="admin", mcp_key_id=None)
    except DouyinError as error:
        raise _http(error) from error
    db.commit()
    background.add_task(jobs.run_job_task, job.id)
    return jobs.job_result_payload(db, job)


@router.get("/jobs/{job_id}")
def admin_detail(job_id: str, db: Session = Depends(get_db)):
    try:
        job = jobs.get_job(db, job_id, None)
    except DouyinError as error:
        raise _http(error) from error
    return jobs.job_result_payload(db, job)


@router.post("/jobs/{job_id}/retry", status_code=202)
def admin_retry(
    job_id: str, background: BackgroundTasks, db: Session = Depends(get_db)
):
    try:
        job = jobs.get_job(db, job_id, None)
        jobs.retry_job(db, job)
    except DouyinError as error:
        raise _http(error) from error
    db.commit()
    background.add_task(jobs.run_job_task, job.id)
    return jobs.job_result_payload(db, job)


@router.delete("/jobs/{job_id}", status_code=204)
def admin_delete(job_id: str, db: Session = Depends(get_db)):
    try:
        job = jobs.get_job(db, job_id, None)
        jobs.delete_job(db, job)
    except DouyinError as error:
        raise _http(error) from error
    db.commit()

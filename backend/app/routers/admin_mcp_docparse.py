from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.capabilities.docparse.errors import DocParseError
from app.capabilities.docparse.jobs import (
    cancel_job,
    create_job,
    delete_job,
    get_owned_job,
    ingest_job,
    job_payload,
    list_jobs,
    retry_job,
    run_job,
)
from app.capabilities.docparse.storage import job_dir, markdown_path
from app.db import get_db
from app.deps import get_current_admin

router = APIRouter(
    prefix="/api/admin/mcp/docparse",
    tags=["admin-mcp-docparse"],
    dependencies=[Depends(get_current_admin)],
)


def _http(error: DocParseError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"error": {"type": error.error_type, "message": error.message}},
    )


@router.get("/jobs")
def admin_list_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    rows, total, counts = list_jobs(db, limit=limit, offset=offset, status=status, q=q)
    return {"items": [job_payload(row, include_markdown=True) for row in rows], "total": total, "counts": counts}


@router.post("/jobs", status_code=201)
async def admin_create_job(file: UploadFile = File(...), db: Session = Depends(get_db)):
    raw = await file.read()
    await file.close()
    job = None
    try:
        job = create_job(db, filename=file.filename or "upload.bin", raw=raw, created_by="admin")
        run_job(db, job, raw)
    except DocParseError as error:
        if job is not None:
            db.commit()
        raise _http(error) from error
    db.commit()
    return job_payload(job)


@router.get("/jobs/{job_id}")
def admin_get_job(job_id: str, db: Session = Depends(get_db)):
    try:
        job = get_owned_job(db, job_id, None)
    except DocParseError as error:
        raise _http(error) from error
    return job_payload(job, include_markdown=job.status == "succeeded")


@router.post("/jobs/{job_id}/cancel")
def admin_cancel_job(job_id: str, db: Session = Depends(get_db)):
    try:
        job = cancel_job(db, get_owned_job(db, job_id, None))
    except DocParseError as error:
        raise _http(error) from error
    db.commit()
    return job_payload(job)


@router.post("/jobs/{job_id}/retry")
def admin_retry_job(job_id: str, db: Session = Depends(get_db)):
    try:
        job = get_owned_job(db, job_id, None)
        retry_job(db, job)
        source = next(job_dir(job.id).glob("source*"), None)
        if source is None or not source.is_file():
            raise DocParseError("源文件已清理，无法重试")
        run_job(db, job, source.read_bytes())
    except DocParseError as error:
        db.commit()
        raise _http(error) from error
    db.commit()
    return job_payload(job)


@router.delete("/jobs/{job_id}", status_code=204)
def admin_delete_job(job_id: str, db: Session = Depends(get_db)):
    try:
        job = get_owned_job(db, job_id, None)
    except DocParseError as error:
        raise _http(error) from error
    delete_job(db, job)
    db.commit()


@router.get("/jobs/{job_id}/download")
def admin_download(job_id: str, db: Session = Depends(get_db)):
    try:
        job = get_owned_job(db, job_id, None)
    except DocParseError as error:
        raise _http(error) from error
    path = markdown_path(job.id)
    if job.status != "succeeded" or not path.is_file():
        raise HTTPException(status_code=404, detail={"error": {"type": "not_found", "message": "结果不存在"}})
    filename = job.source_name.rsplit(".", 1)[0] + ".md"
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=filename)


@router.post("/jobs/{job_id}/ingest")
def admin_ingest(job_id: str, kb_id: str = Form(...), db: Session = Depends(get_db)):
    try:
        job = ingest_job(db, get_owned_job(db, job_id, None), kb_id)
    except DocParseError as error:
        raise _http(error) from error
    db.commit()
    return job_payload(job)

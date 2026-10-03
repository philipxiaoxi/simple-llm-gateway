from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.capabilities.douyin import jobs, storage, tokens
from app.capabilities.douyin.errors import DouyinError
from app.db import get_db
from app.services.mcp_auth import allowed_capability_ids, get_mcp_key_from_headers, resolve_mcp_key
from app.services.mcp_logs import begin_mcp_call

router = APIRouter(prefix="/v1/douyin", tags=["douyin"])


def _error(status: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"type": error_type, "message": message}})


def _mcp_key_dep(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    return get_mcp_key_from_headers(db, authorization, x_api_key)


def _require_douyin(mcp_key) -> bool:
    return "douyin" in allowed_capability_ids(mcp_key)


def _gate(db: Session, mcp_key, operation: str):
    rec = begin_mcp_call(db, mcp_key, "douyin", operation)
    if not _require_douyin(mcp_key):
        rec.failure("MCP Key 未授权能力 douyin")
        return rec, _error(403, "permission_error", "MCP Key 未授权能力 douyin")
    return rec, None


@router.post("/parse", status_code=202)
async def parse(
    background: BackgroundTasks,
    payload: dict[str, Any] | None = None,
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    rec = begin_mcp_call(db, mcp_key, "douyin", "parse")
    if not _require_douyin(mcp_key):
        rec.failure("MCP Key 未授权能力 douyin")
        return _error(403, "permission_error", "MCP Key 未授权能力 douyin")
    body = payload or {}
    raw = str(body.get("url") or body.get("share_text") or "").strip()
    if not raw:
        rec.failure("url 或 share_text 至少填写一个")
        return _error(400, "invalid_request", "url 或 share_text 至少填写一个")
    rehost_value = body.get("rehost", True)
    rehost = True if rehost_value is None else bool(rehost_value)
    try:
        job = jobs.create_job(
            db, raw_input=raw, rehost=rehost, created_by="key", mcp_key_id=mcp_key.id
        )
    except DouyinError as error:
        rec.failure(error.message)
        return _error(error.status_code, error.error_type, error.message)
    rec.success()
    db.commit()
    background.add_task(jobs.run_job_task, job.id)
    return jobs.job_result_payload(db, job)


@router.get("/jobs")
def list_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    rec, denied = _gate(db, mcp_key, "list")
    if denied:
        return denied
    rows, total = jobs.list_jobs(db, mcp_key_id=mcp_key.id, limit=limit, offset=offset)
    rec.success()
    return {"items": [jobs.job_payload(job) for job in rows], "total": total}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    rec, denied = _gate(db, mcp_key, "job")
    if denied:
        return denied
    try:
        job = jobs.get_job(db, job_id, mcp_key.id)
    except DouyinError as error:
        rec.failure(error.message)
        return _error(error.status_code, error.error_type, error.message)
    rec.success()
    return jobs.job_result_payload(db, job)


@router.get("/jobs/{job_id}/result")
def get_result(job_id: str, db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    rec, denied = _gate(db, mcp_key, "result")
    if denied:
        return denied
    try:
        job = jobs.get_job(db, job_id, mcp_key.id)
    except DouyinError as error:
        rec.failure(error.message)
        return _error(error.status_code, error.error_type, error.message)
    rec.success()
    return jobs.job_result_payload(db, job)


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(
    job_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    rec, denied = _gate(db, mcp_key, "retry")
    if denied:
        return denied
    try:
        job = jobs.get_job(db, job_id, mcp_key.id)
        jobs.retry_job(db, job)
    except DouyinError as error:
        rec.failure(error.message)
        return _error(error.status_code, error.error_type, error.message)
    rec.success()
    db.commit()
    background.add_task(jobs.run_job_task, job.id)
    return jobs.job_result_payload(db, job)


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    rec, denied = _gate(db, mcp_key, "delete")
    if denied:
        return denied
    try:
        job = jobs.get_job(db, job_id, mcp_key.id)
        jobs.delete_job(db, job)
    except DouyinError as error:
        rec.failure(error.message)
        return _error(error.status_code, error.error_type, error.message)
    rec.success()
    db.commit()


@router.get("/share")
def share_meta(token: str | None = Query(default=None), db: Session = Depends(get_db)):
    """公开分享元数据：仅凭签名令牌返回标题与播放所需信息，无需登录。"""
    from app.models import DouyinMedia

    media_id = tokens.media_id_from_token(token or "")
    if not media_id:
        return _error(401, "authentication_error", "链接无效或已过期")
    media = db.get(DouyinMedia, media_id)
    if media is None or media.purged or media.status != "ready":
        return _error(410, "expired", "媒体文件已过期或不可用")
    job = media.job
    return {
        "id": media.id,
        "kind": media.kind,
        "title": (job.title if job else "") or "",
        "cover_url": (job.cover_url if job else "") or "",
        "filename": media.filename,
        "content_type": media.content_type,
        "size_bytes": media.size_bytes,
        "download_url": tokens.download_path(media.id, token),
    }


@router.get("/media/{media_id}")
def download_media(
    media_id: str,
    request: Request,
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    from app.models import DouyinMedia

    media = db.get(DouyinMedia, media_id)
    if media is None:
        return _error(404, "not_found", "媒体不存在")
    authorized = False
    raw_key = x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        raw_key = authorization[7:]
    if raw_key:
        key = resolve_mcp_key(db, raw_key)
        if key is not None and key.status == "active" and media.job.mcp_key_id == key.id:
            authorized = True
    if not authorized and token and tokens.verify_token(media_id, token):
        authorized = True
    if not authorized:
        return _error(401, "authentication_error", "需要有效下载令牌或归属 MCP Key")
    if media.purged or media.status != "ready":
        return _error(410, "expired", "媒体文件已过期或不可用")
    path = storage.media_path(media.job_id, media.filename)
    if not path.is_file():
        return _error(410, "expired", "媒体文件已过期或不可用")
    return FileResponse(
        path,
        media_type=media.content_type or "application/octet-stream",
        filename=media.filename or f"{media.index_no:03d}",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=300",
        },
    )

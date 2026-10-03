from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.models import DouyinJob, DouyinMedia

from . import downloader, provider_config, storage, tokens
from .errors import DouyinError
from .extractor import build_extractor
from .resolver import resolve_target

TERMINAL_STATUSES = ("succeeded", "partial", "failed")


class _JobDeadline(Exception):
    """下载过程中到达调用方等待窗口上限，中断当前项以便后续续传。"""


def new_id() -> str:
    return str(uuid4())


def find_media(db: Session, job: DouyinJob) -> list[DouyinMedia]:
    return list(
        db.scalars(
            select(DouyinMedia).where(DouyinMedia.job_id == job.id).order_by(DouyinMedia.index_no)
        ).all()
    )


def get_job(db: Session, job_id: str, mcp_key_id: int | None) -> DouyinJob:
    job = db.get(DouyinJob, job_id)
    if job is None:
        raise DouyinError("任务不存在", status_code=404, error_type="not_found")
    if mcp_key_id is not None and job.mcp_key_id != mcp_key_id:
        raise DouyinError("任务不存在", status_code=404, error_type="not_found")
    return job


def list_jobs(
    db: Session,
    *,
    mcp_key_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    status: str | None = None,
) -> tuple[list[DouyinJob], int]:
    filters = []
    if mcp_key_id is not None:
        filters.append(DouyinJob.mcp_key_id == mcp_key_id)
    if status:
        filters.append(DouyinJob.status == status)
    if q and q.strip():
        like = f"%{q.strip()}%"
        filters.append(DouyinJob.title.like(like) | DouyinJob.author_name.like(like))
    total = int(db.scalar(select(func.count()).select_from(DouyinJob).where(*filters)) or 0)
    rows = list(
        db.scalars(
            select(DouyinJob)
            .where(*filters)
            .order_by(DouyinJob.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return rows, total


def create_job(
    db: Session,
    *,
    raw_input: str,
    rehost: bool = True,
    created_by: str = "admin",
    mcp_key_id: int | None = None,
) -> DouyinJob:
    settings = get_settings()
    if mcp_key_id is not None:
        limit = max(1, int(settings.douyin_max_concurrent_per_key))
        active = int(
            db.scalar(
                select(func.count())
                .select_from(DouyinJob)
                .where(
                    DouyinJob.mcp_key_id == mcp_key_id,
                    DouyinJob.status.in_(("queued", "downloading")),
                )
            )
            or 0
        )
        if active >= limit:
            raise DouyinError(
                "并发下载任务过多，请稍后重试", status_code=429, error_type="too_many_requests"
            )
    target = resolve_target(raw_input)
    job = DouyinJob(
        id=new_id(),
        mcp_key_id=mcp_key_id,
        created_by=created_by,
        raw_input=(raw_input or "")[:4000],
        source_url=target.url,
        extractor="",
        kind="video",
        title="",
        author_name="",
        author_id="",
        cover_url="",
        duration_ms=0,
        rehost=bool(rehost),
        status="queued",
        stage="resolving",
        percent=0,
        message="排队中，等待解析",
        media_count=0,
    )
    db.add(job)
    db.flush()
    return job


def resolve_media(db: Session, job: DouyinJob) -> None:
    """调用提取器解析作品并写入媒体行（后台阶段一：解析中）。"""
    settings = get_settings()
    job.status = "queued"
    job.stage = "resolving"
    job.message = "解析中…"
    job.percent = 0
    job.updated_at = utcnow()
    db.flush()

    target = resolve_target(job.raw_input)
    extractor = build_extractor(provider_config.get_config(db))
    work = extractor.extract(target)
    if not work.media:
        raise DouyinError("未解析到可下载媒体", status_code=422, error_type="content_unavailable")
    if len(work.media) > settings.douyin_max_items:
        raise DouyinError(
            f"媒体项数量超过上限 {settings.douyin_max_items}", status_code=400, error_type="too_many_items"
        )

    job.aweme_id = work.aweme_id or None
    job.source_url = target.url
    job.extractor = work.extractor
    job.kind = work.kind
    job.title = work.title[:2000]
    job.author_name = work.author_name[:128]
    job.author_id = work.author_id[:64]
    job.cover_url = work.cover_url[:1024]
    job.duration_ms = work.duration_ms
    job.media_count = len(work.media)
    db.flush()

    existing = {item.index_no for item in find_media(db, job)}
    for index, item in enumerate(work.media, start=1):
        if index in existing:
            continue
        db.add(
            DouyinMedia(
                id=new_id(),
                job_id=job.id,
                index_no=index,
                kind=item.kind,
                remote_url=item.url[:1024],
                status="pending",
                width=item.width,
                height=item.height,
                duration_ms=item.duration_ms,
            )
        )
    db.flush()


def _fail_job(db: Session, job: DouyinJob, message: str, *, stage_message: str = "解析失败") -> None:
    job.status = "failed"
    job.stage = "done"
    job.percent = 100
    job.message = stage_message
    job.error_message = message[:2000]
    job.finished_at = utcnow()
    job.updated_at = utcnow()
    db.flush()


def run_job(
    db: Session, job: DouyinJob, *, deadline: float | None = None, persist: bool = False
) -> DouyinJob:
    if job.status in TERMINAL_STATUSES:
        return job

    settings = get_settings()

    # 阶段一：解析（后台执行，供轮询展示「解析中」）
    if job.stage == "resolving" and not job.media_count:
        job.status = "queued"
        job.stage = "resolving"
        job.message = "解析中…"
        job.percent = 0
        job.updated_at = utcnow()
        db.flush()
        # 立即提交「解析中」，避免解析耗时期间轮询仍看到创建时的「等待解析」
        if persist:
            db.commit()
        try:
            resolve_media(db, job)
        except DouyinError as error:
            _fail_job(db, job, error.message)
            if persist:
                db.commit()
            raise
        if persist:
            db.commit()

    media = find_media(db, job)
    if not media:
        _fail_job(db, job, "没有可下载的媒体")
        if persist:
            db.commit()
        return job

    # 仅解析：不下载，直接产出原始直链
    if not job.rehost:
        for item in media:
            item.status = "ready"
        job.status = "succeeded"
        job.stage = "done"
        job.percent = 100
        job.success_count = job.media_count
        job.message = "仅解析（未转存）"
        job.finished_at = utcnow()
        job.updated_at = utcnow()
        db.flush()
        if persist:
            db.commit()
        return job

    if job.status != "downloading":
        job.status = "downloading"
        job.stage = "downloading"
        job.started_at = job.started_at or utcnow()
        job.updated_at = utcnow()
        db.flush()

    total = len(media)
    completed_bytes = sum(int(item.size_bytes or 0) for item in media if item.status == "ready")
    finished = sum(1 for item in media if item.status == "ready")
    progress = {"completed": completed_bytes, "finished": finished, "expected": completed_bytes}
    counted: set[str] = {item.id for item in media if item.status == "ready"}
    budget = settings.douyin_max_media_bytes - int(job.total_bytes or 0)
    job.downloaded_bytes = max(int(job.downloaded_bytes or 0), completed_bytes)
    job.expected_bytes = max(int(job.expected_bytes or 0), completed_bytes)
    last_commit = [time.monotonic()]

    def commit_if_due(force: bool = False) -> None:
        if not persist:
            return
        now = time.monotonic()
        if force or now - last_commit[0] >= 0.5:
            last_commit[0] = now
            job.updated_at = utcnow()
            db.commit()

    for item in media:
        if item.status != "pending":
            continue
        if deadline is not None and time.monotonic() > deadline:
            break
        if budget <= 0:
            item.status = "failed"
            item.error_message = "超出任务总字节上限"
            db.flush()
            continue
        limit = max(1, min(settings.douyin_max_item_bytes, budget))

        def on_progress(written: int, length: int | None, item=item) -> None:
            if deadline is not None and time.monotonic() > deadline:
                raise _JobDeadline
            job.downloaded_bytes = progress["completed"] + written
            if length and item.id not in counted:
                counted.add(item.id)
                progress["expected"] += length
                job.expected_bytes = progress["expected"]
            fraction = min(1.0, written / length) if length else 0.0
            if total:
                job.percent = min(
                    99,
                    max(job.percent or 0, int((progress["finished"] + fraction) / total * 100)),
                )
            job.message = f"下载中 第{min(progress['finished'] + 1, total)}/{total} 项"
            commit_if_due()

        dest = storage.media_path(job.id, f"{item.index_no:03d}.bin")
        try:
            size, content_type, sha = downloader.fetch_to_file(
                item.remote_url, dest, max_bytes=limit, kind=item.kind, on_progress=on_progress
            )
            filename = downloader.final_filename(item.index_no, content_type, item.kind)
            final_path = storage.media_path(job.id, filename)
            if final_path != dest and dest.exists():
                dest.replace(final_path)
            item.filename = filename
            item.content_type = content_type
            item.size_bytes = size
            item.sha256 = sha
            item.status = "ready"
            item.error_message = None
            job.total_bytes = int(job.total_bytes or 0) + size
            budget -= size
            progress["completed"] += size
            if item.id not in counted:
                counted.add(item.id)
                progress["expected"] += size
            progress["finished"] += 1
            job.downloaded_bytes = progress["completed"]
            job.expected_bytes = progress["expected"]
            job.percent = min(99, int(progress["finished"] / total * 100)) if total else 0
            job.message = f"下载中 {progress['finished']}/{total}"
        except _JobDeadline:
            db.flush()
            break
        except DouyinError as error:
            item.status = "failed"
            item.error_message = error.message[:500]
        except Exception as error:  # noqa: BLE001
            item.status = "failed"
            item.error_message = str(error)[:500]
        db.flush()
        commit_if_due(force=True)

    ready = sum(1 for item in media if item.status == "ready")
    failed = sum(1 for item in media if item.status == "failed")
    pending = sum(1 for item in media if item.status == "pending")
    job.success_count = ready
    job.media_count = total
    job.downloaded_bytes = progress["completed"]
    job.expected_bytes = progress["expected"]
    job.updated_at = utcnow()
    if pending:
        job.status = "downloading"
        job.stage = "downloading"
        job.message = f"下载中 {ready + failed}/{total}"
    else:
        job.finished_at = utcnow()
        job.stage = "done"
        job.percent = 100
        if ready and failed:
            job.status = "partial"
            job.message = f"部分完成 {ready}/{total}"
        elif ready:
            job.status = "succeeded"
            job.message = "转存完成"
        else:
            job.status = "failed"
            job.message = "转存失败"
            job.error_message = next(
                (item.error_message for item in media if item.error_message), "全部媒体项转存失败"
            )
    db.flush()
    if persist:
        db.commit()
    return job


def run_job_task(job_id: str, *, deadline_seconds: float | None = None) -> None:
    """BackgroundTasks 入口：使用独立 session 执行转存。"""
    from app.db import get_session_factory

    session = get_session_factory()()
    try:
        job = session.get(DouyinJob, job_id)
        if job is None:
            return
        deadline = time.monotonic() + deadline_seconds if deadline_seconds else None
        try:
            run_job(session, job, deadline=deadline, persist=True)
        except Exception as error:  # noqa: BLE001
            job.status = "failed"
            job.stage = "done"
            job.percent = 100
            job.message = "转存失败"
            job.error_message = str(error)[:2000]
            job.finished_at = utcnow()
            job.updated_at = utcnow()
        session.commit()
    finally:
        session.close()


def retry_job(db: Session, job: DouyinJob) -> DouyinJob:
    if job.status not in ("failed", "partial"):
        raise DouyinError("只有失败或部分完成的任务可以重试", status_code=400, error_type="invalid_request")
    for item in find_media(db, job):
        if item.status != "ready":
            item.status = "pending"
            item.error_message = None
    job.status = "queued"
    job.stage = "resolving"
    job.percent = 0
    job.message = "已重新入队"
    job.error_message = None
    job.success_count = 0
    job.total_bytes = 0
    job.downloaded_bytes = 0
    job.expected_bytes = 0
    job.finished_at = None
    job.started_at = None
    job.updated_at = utcnow()
    db.flush()
    return job


def delete_job(db: Session, job: DouyinJob) -> None:
    storage.purge_job(job.id)
    db.delete(job)
    db.flush()


def cleanup_expired(db: Session) -> int:
    from datetime import timedelta

    settings = get_settings()
    days = max(0, int(settings.douyin_retention_days))
    if not days:
        return 0
    cutoff = utcnow() - timedelta(days=days)
    removed = 0
    seen: set[str] = set()
    rows = db.scalars(
        select(DouyinMedia).where(DouyinMedia.purged == 0, DouyinMedia.status == "ready")
    ).all()
    for item in rows:
        if item.job_id in seen:
            continue
        job = db.get(DouyinJob, item.job_id)
        if job is None:
            continue
        if not job.created_at or job.created_at >= cutoff:
            continue
        seen.add(job.id)
        storage.purge_job(job.id)
        for sibling in find_media(db, job):
            sibling.purged = 1
        removed += 1
    if removed:
        db.flush()
    return removed


def reconcile_stuck(db: Session) -> int:
    rows = db.scalars(
        select(DouyinJob).where(DouyinJob.status.in_(("queued", "downloading")))
    ).all()
    changed = 0
    for job in rows:
        job.status = "failed"
        job.stage = "done"
        job.percent = 100
        job.message = "转存失败"
        job.error_message = "进程重启，请重试"
        job.finished_at = utcnow()
        job.updated_at = utcnow()
        changed += 1
    if changed:
        db.flush()
    return changed


# ---- payloads ----


def media_payload(media: DouyinMedia) -> dict[str, Any]:
    downloadable = media.status == "ready" and not media.purged and bool(media.job and media.job.rehost)
    token = tokens.make_token(media.id) if downloadable else None
    return {
        "id": media.id,
        "index_no": media.index_no,
        "kind": media.kind,
        "status": media.status,
        "content_type": media.content_type,
        "size_bytes": media.size_bytes,
        "width": media.width,
        "height": media.height,
        "duration_ms": media.duration_ms,
        "original_url": media.remote_url,
        "filename": media.filename,
        # 相对路径：浏览器按当前 origin 解析，经前端/反代访问也能直接下载。
        "download_url": tokens.download_path(media.id, token) if downloadable else None,
        "absolute_download_url": tokens.download_url(media.id, token) if downloadable else None,
        "token": token,
        "error_message": media.error_message,
        "purged": bool(media.purged),
    }


def job_payload(job: DouyinJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "stage": job.stage,
        "percent": job.percent,
        "message": job.message,
        "extractor": job.extractor,
        "kind": job.kind,
        "aweme_id": job.aweme_id,
        "title": job.title,
        "author_name": job.author_name,
        "author_id": job.author_id,
        "cover_url": job.cover_url,
        "duration_ms": job.duration_ms,
        "rehost": bool(job.rehost),
        "media_count": job.media_count,
        "success_count": job.success_count,
        "total_bytes": job.total_bytes,
        "downloaded_bytes": job.downloaded_bytes,
        "expected_bytes": job.expected_bytes,
        "source_url": job.source_url,
        "error_message": job.error_message,
        "created_by": job.created_by,
        "mcp_key_id": job.mcp_key_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def job_result_payload(db: Session, job: DouyinJob) -> dict[str, Any]:
    body = job_payload(job)
    body["media"] = [media_payload(item) for item in find_media(db, job)]
    return body

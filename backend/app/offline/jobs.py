"""离线下载的后台缓存队列。

解析后只入队，不在请求里同步下载。worker 轮询 `offline_downloads` 中 `queued`
的任务，异步下载并按字节进度写回，完成后置为 `ready`，失败置为 `failed`。
大文件下载不会阻塞管理端/公开端的请求。
"""

from __future__ import annotations

import asyncio
import json
import time

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.db import get_session_factory
from app.models import OfflineDownload
from app.offline import cache, chrome, docker, edge, msstore, vscode
from app.offline.errors import OfflineError
from app.offline.http import fetch_bytes

MAX_ATTEMPTS = 3
_PROGRESS_MIN_INTERVAL = 0.4

_worker_tasks: list[asyncio.Task[None]] = []


def _write_progress(
    row_id: int,
    *,
    downloaded: int | None = None,
    expected: int | None = None,
    percent: int | None = None,
    stage: str | None = None,
    message: str | None = None,
) -> None:
    session = get_session_factory()()
    try:
        row = session.get(OfflineDownload, row_id)
        if row is None or cache.status_of(row) != "caching":
            return
        cache.update_progress(
            session,
            row,
            downloaded=downloaded,
            expected=expected,
            percent=percent,
            stage=stage,
            message=message,
        )
        session.commit()
    except Exception:  # noqa: BLE001 - 进度写失败不影响下载
        session.rollback()
    finally:
        session.close()


def _progress_updater(row_id: int):
    """生成带节流的进度回调：provider 传 (已下载字节, 期望字节)。"""

    state = {"at": 0.0, "percent": -1}

    def update(downloaded: int, expected: int) -> None:
        downloaded = int(downloaded or 0)
        expected = int(expected or 0)
        percent = int(downloaded * 100 / expected) if expected > 0 else 0
        if percent >= 100:
            percent = 99  # 最终 100 由 finalize 写入
        now = time.monotonic()
        if percent == state["percent"] and now - state["at"] < 1.0:
            return
        if now - state["at"] < _PROGRESS_MIN_INTERVAL and percent not in (0, 99):
            return
        state["at"] = now
        state["percent"] = percent
        _write_progress(row_id, downloaded=downloaded, expected=expected, percent=percent)

    return update


async def _icon_bytes(row_id: int) -> bytes | None:
    session = get_session_factory()()
    try:
        row = session.get(OfflineDownload, row_id)
        icon_url = row.icon_url if row is not None else ""
    finally:
        session.close()
    if not icon_url:
        return None
    return await fetch_bytes(icon_url)


def _finalize_file(row_id: int, src_path: str, filename: str, content_type: str, icon: bytes | None) -> None:
    session = get_session_factory()()
    try:
        row = session.get(OfflineDownload, row_id)
        if row is None:
            return
        cache.finalize_file(
            session,
            row,
            src_path=src_path,
            filename=filename,
            content_type=content_type,
            icon_bytes=icon,
            icon_url=row.icon_url,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _finalize_bytes(row_id: int, data: bytes, filename: str, content_type: str, icon: bytes | None) -> None:
    session = get_session_factory()()
    try:
        row = session.get(OfflineDownload, row_id)
        if row is None:
            return
        cache.finalize_bytes(
            session,
            row,
            data=data,
            filename=filename,
            content_type=content_type,
            icon_bytes=icon,
            icon_url=row.icon_url,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _fail(row_id: int, message: str) -> None:
    session = get_session_factory()()
    try:
        row = session.get(OfflineDownload, row_id)
        if row is None:
            return
        cache.mark_failed(session, row, message)
        session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()
    finally:
        session.close()


def _claim_next() -> int | None:
    session = get_session_factory()()
    try:
        row = session.scalar(
            select(OfflineDownload).where(OfflineDownload.status == "queued").order_by(OfflineDownload.id).limit(1)
        )
        if row is None:
            return None
        if int(row.attempts or 0) >= MAX_ATTEMPTS:
            cache.mark_failed(session, row, "重试次数已用尽")
            session.commit()
            return None
        # 条件更新做原子认领，避免多个 worker 抢到同一条任务重复下载
        claimed = session.execute(
            update(OfflineDownload)
            .where(OfflineDownload.id == row.id, OfflineDownload.status == "queued")
            .values(
                status="caching",
                stage="download",
                percent=1,
                message="开始缓存",
                error_message=None,
                bytes_downloaded=0,
                attempts=OfflineDownload.attempts + 1,
                updated_at=utcnow(),
            )
        )
        if claimed.rowcount == 0:
            session.rollback()
            return None
        session.commit()
        return int(row.id)
    finally:
        session.close()


async def _run_vscode(row_id: int, request: dict) -> None:
    publisher = str(request.get("publisher") or "")
    extension = str(request.get("extension") or "")
    version = str(request.get("version") or "")
    if not (publisher and extension and version):
        raise OfflineError("缺少插件标识，无法缓存", status_code=409)
    path, filename = await vscode.download_vsix(publisher, extension, version, on_progress=_progress_updater(row_id))
    _finalize_file(row_id, path, filename, "application/octet-stream", None)


async def _run_crx(row_id: int, request: dict, *, use_edge: bool) -> None:
    extension_id = str(request.get("id") or "")
    fmt = str(request.get("format") or "crx")
    if not extension_id:
        raise OfflineError("缺少扩展 ID，无法缓存", status_code=409)
    module = edge if use_edge else chrome
    data, filename, content_type = await module.download(extension_id, fmt, on_progress=_progress_updater(row_id))
    icon = await _icon_bytes(row_id)
    _finalize_bytes(row_id, data, filename, content_type, icon)


async def _run_docker(row_id: int, request: dict) -> None:
    ref = docker.parse_reference(str(request.get("query") or ""))
    platform = request.get("platform") or None
    path, filename = await docker.build_image_tar(
        ref["namespace"], ref["repository"], ref["tag"], platform, on_progress=_progress_updater(row_id)
    )
    _finalize_file(row_id, path, filename, "application/x-tar", None)


async def _run_msstore(row_id: int, request: dict) -> None:
    url = str(request.get("url") or "")
    filename = str(request.get("filename") or "")
    if not url:
        raise OfflineError("缺少下载地址，无法缓存", status_code=409)
    path, name, content_type = await msstore.fetch_to_file(url, filename, on_progress=_progress_updater(row_id))
    _finalize_file(row_id, path, name, content_type, None)


async def _process_job(row_id: int) -> None:
    """处理一个已置为 caching 的任务。"""
    session = get_session_factory()()
    try:
        row = session.get(OfflineDownload, row_id)
        if row is None:
            return
        provider = row.provider
        try:
            request = json.loads(row.request_json or "{}")
        except ValueError:
            request = {}
    finally:
        session.close()

    try:
        if provider == "vscode":
            await _run_vscode(row_id, request)
        elif provider == "chrome":
            await _run_crx(row_id, request, use_edge=False)
        elif provider == "edge":
            await _run_crx(row_id, request, use_edge=True)
        elif provider == "docker":
            await _run_docker(row_id, request)
        elif provider == "msstore":
            await _run_msstore(row_id, request)
        else:
            raise OfflineError(f"不支持的来源: {provider}", status_code=400)
    except OfflineError as error:
        _fail(row_id, error.message)
    except Exception as error:  # noqa: BLE001
        _fail(row_id, f"缓存失败：{str(error)[:300]}")


async def process_job_now(row_id: int) -> None:
    """供测试/手动触发：把任务置为 caching 后处理一次。"""
    session = get_session_factory()()
    try:
        row = session.get(OfflineDownload, row_id)
        if row is None or cache.status_of(row) == "ready":
            return
        cache.mark_caching(session, row)
        session.commit()
    finally:
        session.close()
    await _process_job(row_id)


async def _worker_loop() -> None:
    while True:
        try:
            row_id = _claim_next()
            if row_id is None:
                await asyncio.sleep(1.0)
                continue
            await _process_job(row_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            print(f"[offline-jobs] worker error: {error}")
            await asyncio.sleep(1.0)


def start_offline_job_workers() -> list[asyncio.Task[None]]:
    settings = get_settings()
    concurrency = max(1, min(int(settings.offline_cache_concurrency or 1), 4))
    global _worker_tasks
    if _worker_tasks:
        return _worker_tasks
    _worker_tasks = [asyncio.create_task(_worker_loop()) for _ in range(concurrency)]
    return _worker_tasks


def reset_offline_job_workers() -> None:
    global _worker_tasks
    _worker_tasks = []


def reconcile_stuck_jobs(db: Session) -> int:
    """进程重启后，把中断的 caching 任务重新排队。"""
    rows = db.scalars(select(OfflineDownload).where(OfflineDownload.status == "caching")).all()
    for row in rows:
        row.status = "queued"
        row.stage = "queued"
        row.percent = 0
        row.message = "服务重启，重新排队"
        row.updated_at = utcnow()
    if rows:
        db.commit()
    return len(rows)

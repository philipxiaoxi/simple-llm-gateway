from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.db import get_session_factory
from app.models import DesktopTool, DesktopToolRun, DesktopToolRunLog

_jobs: dict[int, asyncio.Task[None]] = {}
_processes: dict[int, asyncio.subprocess.Process] = {}
_logs: dict[int, list[str]] = defaultdict(list)
_stopped: set[int] = set()
_run_ids: dict[int, int] = {}


def has_active_job(tool_id: int) -> bool:
    task = _jobs.get(tool_id)
    return task is not None and not task.done()


def download_environment() -> dict[str, str]:
    allowed = {
        "APPDATA", "COMSPEC", "HOME", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA",
        "NO_PROXY", "PATH", "PATHEXT", "PROGRAMDATA", "PROGRAMFILES", "SYSTEMROOT",
        "TEMP", "TMP", "USERPROFILE", "WINDIR", "HTTP_PROXY", "HTTPS_PROXY",
        "DISPLAY", "WAYLAND_DISPLAY", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR", "LANG", "LC_ALL", "LC_CTYPE",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


def append_run_log(run_id: int, item_id: int, line: str) -> None:
    _logs[item_id].append(line)
    session = get_session_factory()()
    try:
        last_number = session.scalar(
            select(DesktopToolRunLog.line_number)
            .where(DesktopToolRunLog.run_id == run_id)
            .order_by(DesktopToolRunLog.line_number.desc())
            .limit(1)
        ) or 0
        session.add(DesktopToolRunLog(run_id=run_id, line_number=last_number + 1, line=line))
        session.commit()
    finally:
        session.close()


def append_run_log_safely(run_id: int, item_id: int, line: str) -> None:
    try:
        append_run_log(run_id, item_id, line)
    except Exception:
        # 日志数据库故障不能中断 stdout 收集或遗留下载进程。
        return


def finish_run(run_id: int, status: str, error_message: str | None = None) -> None:
    session = get_session_factory()()
    try:
        run = session.get(DesktopToolRun, run_id)
        if run is not None:
            run.status = status
            run.error_message = error_message
            run.finished_at = utcnow()
            session.commit()
    finally:
        session.close()


def fail_running_runs(tool_id: int, message: str) -> None:
    session = get_session_factory()()
    try:
        runs = session.scalars(
            select(DesktopToolRun).where(
                DesktopToolRun.tool_id == tool_id,
                DesktopToolRun.status == "running",
            )
        ).all()
        for run in runs:
            run.status = "failed"
            run.error_message = message
            run.finished_at = utcnow()
        if runs:
            session.commit()
    finally:
        session.close()


def tool_dict(item: DesktopTool) -> dict:
    return {"id": item.id, "tool_id": item.tool_id, "platform": item.platform, "name": item.name,
            "description": item.description, "icon": item.icon, "script_name": item.script_name,
            "status": item.status, "file_name": item.file_name, "file_size": item.file_size,
            "version": item.version, "error_message": item.error_message, "updated_at": item.updated_at}


def script_path(item: DesktopTool) -> Path:
    return get_settings().resolved_tools_path / "scripts" / item.script_name


def logs_for(tool_id: int) -> list[str]:
    return _logs.get(tool_id, [])


def active_run_id(tool_id: int) -> int | None:
    return _run_ids.get(tool_id)


def persisted_logs_for(run_id: int) -> list[str]:
    session = get_session_factory()()
    try:
        return list(session.scalars(
            select(DesktopToolRunLog.line)
            .where(DesktopToolRunLog.run_id == run_id)
            .order_by(DesktopToolRunLog.line_number)
        ))
    finally:
        session.close()


def reconcile_stuck_downloads(db: Session) -> None:
    """后端重启后，把数据库里仍标记为 downloading 的工具复位。

    下载任务的状态（_jobs/_processes/_logs）只保存在内存中，进程重启后即丢失，
    但数据库里的 status 仍可能是 downloading，导致工具永久卡在“下载中”且无日志。
    这里把这类工具复位为 not_downloaded，让用户可以重新发起预下载。
    """
    stuck = db.scalars(select(DesktopTool).where(DesktopTool.status == "downloading")).all()
    stuck_ids = {item.id for item in stuck}
    for item in stuck:
        item.status = "not_downloaded"
        item.error_message = None
        item.updated_at = utcnow()
    if stuck_ids:
        runs = db.scalars(
            select(DesktopToolRun).where(
                DesktopToolRun.tool_id.in_(stuck_ids),
                DesktopToolRun.status == "running",
            )
        ).all()
        for run in runs:
            run.status = "failed"
            run.error_message = "后端重启，执行任务已中断"
            run.finished_at = utcnow()
    if stuck:
        db.commit()


def job_done(tool_id: int) -> bool:
    task = _jobs.get(tool_id)
    return task is not None and task.done()


def is_running(tool_id: int) -> bool:
    return has_active_job(tool_id)


def _run_process_sync(
    command: list[str],
    env: dict[str, str],
    item_id: int,
    run_id: int,
    timeout: int,
) -> tuple[list[str], int]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    lines: list[str] = []
    reader_errors: list[str] = []
    _processes[item_id] = process  # type: ignore[assignment]
    reader_done = threading.Event()

    def read_output() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                line = line.rstrip()
                lines.append(line)
                append_run_log_safely(run_id, item_id, line)
        except Exception as error:
            message = f"输出读取失败: {type(error).__name__}: {error!r}"
            reader_errors.append(message)
            lines.append(message)
            append_run_log_safely(run_id, item_id, message)
        finally:
            reader_done.set()

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if time.monotonic() >= deadline:
                process.kill()
                process.wait()
                lines.append("执行超时")
                append_run_log_safely(run_id, item_id, "执行超时")
                break
            time.sleep(0.1)
        reader_done.wait(timeout=5)
        if reader_errors:
            return lines, process.returncode or -1
        return lines, process.returncode or 0
    finally:
        _processes.pop(item_id, None)


async def start_download(db: Session, item: DesktopTool) -> None:
    if item.status == "downloading" or is_running(item.id):
        raise RuntimeError("下载中")
    fail_running_runs(item.id, "任务已失联，已由新的执行任务替代")
    path = script_path(item)
    if not path.is_file():
        raise RuntimeError("脚本不存在，请先上传或保存脚本")
    output_dir = get_settings().resolved_tools_path / "downloads" / f"{item.tool_id}-{item.platform}"
    if shutil.disk_usage(output_dir.parent).free < 100 * 1024 * 1024:
        raise RuntimeError("磁盘可用空间不足 100MB")
    item.status = "downloading"
    item.error_message = None
    item.file_path = None
    db.commit()
    _logs[item.id] = []
    _stopped.discard(item.id)
    run = DesktopToolRun(tool_id=item.id)
    db.add(run)
    db.commit()
    db.refresh(run)
    _run_ids[item.id] = run.id
    _jobs[item.id] = asyncio.create_task(_run(item.id, run.id, item.tool_id, item.platform, path, output_dir))


async def stop_download(db: Session, item: DesktopTool) -> None:
    if not is_running(item.id):
        if item.status == "downloading":
            item.status = "failed"
            item.error_message = "下载任务已失联，请重新发起预下载"
            item.updated_at = utcnow()
            db.commit()
            run_id = _run_ids.get(item.id)
            if run_id:
                append_run_log(run_id, item.id, "下载任务已失联，请重新发起预下载")
                finish_run(run_id, "failed", "下载任务已失联，请重新发起预下载")
                _run_ids.pop(item.id, None)
            return
        raise RuntimeError("当前没有正在执行的下载任务")
    _stopped.add(item.id)
    process = _processes.get(item.id)
    if process is not None and process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    run_id = _run_ids.get(item.id)
    if run_id:
        append_run_log(run_id, item.id, "已请求停止，正在终止脚本…")
    item.status = "failed"
    item.error_message = "已手动停止"
    item.updated_at = utcnow()
    db.commit()


async def _run(item_id: int, run_id: int, tool_id: str, platform: str, script: Path, output_dir: Path) -> None:
    lines: list[str] = []
    process: asyncio.subprocess.Process | None = None
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"tool_id": tool_id, "platform": platform, "output_dir": str(output_dir)})
        # 强制子进程使用 UTF-8 输出，避免 Windows 下 GBK 编码导致中文乱码
        env = download_environment()
        command = [sys.executable, str(script), payload]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except NotImplementedError:
            lines, return_code = await asyncio.to_thread(
                _run_process_sync,
                command,
                env,
                item_id,
                run_id,
                get_settings().tools_download_timeout_seconds,
            )
            process = None
        if process is not None:
            _processes[item_id] = process

            async def consume() -> None:
                assert process.stdout is not None
                async for raw_line in process.stdout:
                    line = raw_line.decode("utf-8", errors="replace").rstrip()
                    lines.append(line)
                    append_run_log_safely(run_id, item_id, line)

            try:
                await asyncio.wait_for(consume(), timeout=get_settings().tools_download_timeout_seconds)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                lines.append("执行超时")
                append_run_log_safely(run_id, item_id, "执行超时")
            return_code = await process.wait()
        else:
            _processes.pop(item_id, None)
    except Exception as error:
        message = f"任务启动或执行失败: {type(error).__name__}: {error!r}"
        lines.append(message)
        append_run_log_safely(run_id, item_id, message)
        return_code = -1
    except asyncio.CancelledError:
        finish_run(run_id, "failed", "任务被取消")
        raise
    finally:
        _processes.pop(item_id, None)

    # 手动停止时，stop_download 已写入状态，这里不再覆盖
    if item_id in _stopped:
        finish_run(run_id, "stopped", "已手动停止")
        _run_ids.pop(item_id, None)
        return

    text = "\n".join(lines)
    session = get_session_factory()()
    item = session.get(DesktopTool, item_id)
    try:
        result = None
        for line in reversed(text.splitlines()):
            try:
                result = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        file_path = Path(result.get("file_path", "")) if isinstance(result, dict) else Path()
        if return_code != 0 or not isinstance(result, dict) or result.get("status") != "success":
            raise RuntimeError(
                (result or {}).get("error_message")
                or text[-4000:]
                or f"脚本未输出有效结果 (exit_code={return_code})"
            )
        if not file_path.is_file() or not file_path.resolve().is_relative_to(get_settings().resolved_tools_path.resolve() / "downloads"):
            raise RuntimeError("脚本返回的文件不存在或不在缓存目录")
        item.status, item.file_path = "downloaded", str(file_path)
        item.file_name = file_path.name
        item.file_size = file_path.stat().st_size
        item.version = result.get("version")
        item.error_message = None
    except Exception as error:
        item.status, item.error_message = "failed", str(error)
    item.updated_at = utcnow()
    run_status = "downloaded" if item.status == "downloaded" else "failed"
    run_error_message = item.error_message
    session.commit()
    session.close()
    finish_run(run_id, run_status, run_error_message)
    _run_ids.pop(item_id, None)
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, get_session_factory
from app.deps import get_current_admin
from app.models import DesktopTool, DesktopToolRun, DesktopToolRunLog
from app.schemas import DesktopToolCreate, DesktopToolOut, DesktopToolRunDetail, DesktopToolRunOut, DesktopToolUpdate
from app.services.desktop_tools import active_run_id, has_active_job, job_done, logs_for, persisted_logs_for, script_path, start_download, stop_download, tool_dict

router = APIRouter(prefix="/api/admin/tools", tags=["admin-tools"], dependencies=[Depends(get_current_admin)])
_SAFE_TOOL_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

@router.post("", response_model=DesktopToolOut, status_code=201)
def create_tool(payload: DesktopToolCreate, db: Session = Depends(get_db)):
    tool_id = payload.tool_id.strip()
    platform = payload.platform.strip().lower()
    name = payload.name.strip()
    script = payload.script.encode("utf-8")
    if not tool_id or not platform or not name:
        raise HTTPException(400, "工具 ID、平台和名称不能为空")
    if not _SAFE_TOOL_PART.fullmatch(tool_id) or not _SAFE_TOOL_PART.fullmatch(platform):
        raise HTTPException(400, "工具 ID 和平台只能包含字母、数字、点、下划线和连字符")
    if not script or len(script) > 1024 * 1024:
        raise HTTPException(400, "脚本不能为空且不能超过 1MB")
    if db.scalar(select(DesktopTool).where(DesktopTool.tool_id == tool_id, DesktopTool.platform == platform)):
        raise HTTPException(409, "同一工具和平台已登记")
    item = DesktopTool(
        tool_id=tool_id,
        platform=platform,
        name=name,
        description=payload.description.strip(),
        icon=payload.icon,
        script_name=f"{tool_id}-{platform}.py",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    script_path(item).write_bytes(script)
    return DesktopToolOut(**tool_dict(item))

@router.get("", response_model=list[DesktopToolOut])
def list_tools(db: Session = Depends(get_db)):
    return [DesktopToolOut(**tool_dict(item)) for item in db.scalars(select(DesktopTool).order_by(DesktopTool.name)).all()]

@router.patch("/{tool_id}", response_model=DesktopToolOut)
def update_tool(tool_id: int, payload: DesktopToolUpdate, db: Session = Depends(get_db)):
    item = db.get(DesktopTool, tool_id)
    if item is None: raise HTTPException(404, "工具不存在")
    values = payload.model_dump(exclude_unset=True)
    for key in ("platform",):
        value = values.get(key)
        if value is not None:
            value = value.strip().lower()
            if not _SAFE_TOOL_PART.fullmatch(value):
                raise HTTPException(400, "平台只能包含字母、数字、点、下划线和连字符")
            values[key] = value
    for key, value in values.items():
        setattr(item, key, value)
    db.commit(); db.refresh(item)
    return DesktopToolOut(**tool_dict(item))

@router.post("/{tool_id}/script")
async def upload_script(tool_id: int, script: UploadFile | None = File(default=None), content: str = Form(default=""), db: Session = Depends(get_db)):
    item = db.get(DesktopTool, tool_id)
    if item is None: raise HTTPException(404, "工具不存在")
    target = script_path(item)
    data = await script.read() if script else content.encode()
    if not data or len(data) > 1024 * 1024: raise HTTPException(400, "脚本不能为空且不能超过 1MB")
    target.write_bytes(data)
    return {"ok": True, "script": data.decode("utf-8", errors="replace")}

@router.get("/{tool_id}/script")
def get_script(tool_id: int, db: Session = Depends(get_db)):
    item = db.get(DesktopTool, tool_id)
    if item is None: raise HTTPException(404, "工具不存在")
    return {"script": script_path(item).read_text(encoding="utf-8") if script_path(item).is_file() else ""}

@router.post("/{tool_id}/pre-download", response_model=DesktopToolOut)
async def pre_download(tool_id: int, db: Session = Depends(get_db)):
    item = db.get(DesktopTool, tool_id)
    if item is None: raise HTTPException(404, "工具不存在")
    try: await start_download(db, item)
    except RuntimeError as error: raise HTTPException(409, str(error)) from error
    return DesktopToolOut(**tool_dict(item))

@router.post("/{tool_id}/stop", response_model=DesktopToolOut)
async def stop(tool_id: int, db: Session = Depends(get_db)):
    item = db.get(DesktopTool, tool_id)
    if item is None: raise HTTPException(404, "工具不存在")
    try: await stop_download(db, item)
    except RuntimeError as error: raise HTTPException(409, str(error)) from error
    return DesktopToolOut(**tool_dict(item))

@router.get("/{tool_id}/events")
async def events(tool_id: int, db: Session = Depends(get_db)):
    if db.get(DesktopTool, tool_id) is None: raise HTTPException(404, "工具不存在")
    async def stream():
        sent = 0
        while True:
            run_id = active_run_id(tool_id)
            if run_id is not None:
                lines = persisted_logs_for(run_id)
            else:
                session = get_session_factory()()
                try:
                    run = session.scalar(
                        select(DesktopToolRun)
                        .where(DesktopToolRun.tool_id == tool_id)
                        .order_by(DesktopToolRun.started_at.desc())
                        .limit(1)
                    )
                    lines = persisted_logs_for(run.id) if run is not None else logs_for(tool_id)
                finally:
                    session.close()
            while sent < len(lines):
                yield f"data: {json.dumps({'line': lines[sent]}, ensure_ascii=False)}\n\n"
                sent += 1
            yield ": ping\n\n"
            if (job_done(tool_id) or not has_active_job(tool_id)) and sent >= len(lines):
                break
            await asyncio.sleep(0.5)
    return StreamingResponse(stream(), media_type="text/event-stream")


def run_dict(run: DesktopToolRun) -> dict:
    return {"id": run.id, "tool_id": run.tool_id, "status": run.status, "error_message": run.error_message,
            "started_at": run.started_at, "finished_at": run.finished_at}


@router.get("/{tool_id}/runs", response_model=list[DesktopToolRunOut])
def list_runs(tool_id: int, limit: int = 20, db: Session = Depends(get_db)):
    if db.get(DesktopTool, tool_id) is None:
        raise HTTPException(404, "工具不存在")
    limit = max(1, min(limit, 100))
    runs = db.scalars(
        select(DesktopToolRun)
        .where(DesktopToolRun.tool_id == tool_id)
        .order_by(DesktopToolRun.started_at.desc())
        .limit(limit)
    ).all()
    return [run_dict(run) for run in runs]


@router.get("/{tool_id}/runs/{run_id}", response_model=DesktopToolRunDetail)
def get_run(tool_id: int, run_id: int, db: Session = Depends(get_db)):
    run = db.scalar(select(DesktopToolRun).where(DesktopToolRun.id == run_id, DesktopToolRun.tool_id == tool_id))
    if run is None:
        raise HTTPException(404, "执行记录不存在")
    return {**run_dict(run), "lines": persisted_logs_for(run.id)}

@router.get("/{tool_id}/download")
def download(tool_id: int, db: Session = Depends(get_db)):
    item = db.get(DesktopTool, tool_id)
    if item is None or item.status != "downloaded" or not item.file_path: raise HTTPException(409, "工具尚未预下载成功")
    path = Path(item.file_path)
    download_root = get_settings().resolved_tools_path.joinpath("downloads").resolve()
    if not path.is_file() or not path.resolve().is_relative_to(download_root):
        raise HTTPException(404, "缓存文件不存在")
    return FileResponse(path, filename=item.file_name or path.name)
"""图片 / 视频生成的管理接口。

`router` 需要管理员登录；`asset_router` 用签名令牌鉴权（浏览器 <img>/<video> 带不了
Authorization 头），与资讯媒体同一套令牌方案。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.db import get_db
from app.deps import get_current_admin
from app.generation import service, storage
from app.info.tokens import media_id_from_token
from app.models import GenerationAsset, GenerationTask

router = APIRouter(
    prefix="/api/admin/generation",
    tags=["admin-generation"],
    dependencies=[Depends(get_current_admin)],
)

asset_router = APIRouter(prefix="/api/admin/generation", tags=["admin-generation-assets"])


def _detail(payload: dict[str, str]) -> dict[str, object]:
    return {"error": {"type": "invalid_request", "message": payload["message"]}}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return service.settings_payload(db)


@router.put("/settings")
def put_settings(payload: dict | None = None, db: Session = Depends(get_db)):
    try:
        return service.update_settings(db, payload or {})
    except service.GenerationError as error:
        raise HTTPException(status_code=error.status_code, detail=_detail({"message": error.message})) from error


@router.post("/providers/{provider}/key")
def clear_provider_key(provider: str, db: Session = Depends(get_db)):
    service.clear_provider_key(db, provider)
    return service.settings_payload(db)


@router.post("/uploads", status_code=201)
async def upload_input(file: UploadFile = File(...), db: Session = Depends(get_db)):
    data = await file.read()
    try:
        asset = service.create_upload(
            db,
            filename=file.filename or "upload",
            content_type=file.content_type or "",
            data=data,
        )
    except service.GenerationError as error:
        raise HTTPException(status_code=error.status_code, detail=_detail({"message": error.message})) from error
    return service.asset_to_dict(asset)


@router.delete("/uploads/{asset_id}", status_code=204)
def delete_upload(asset_id: str, db: Session = Depends(get_db)):
    if not service.delete_uploaded_input(db, asset_id):
        raise HTTPException(status_code=404, detail="参考文件不存在")


@router.post("/tasks", status_code=201)
def create_task(payload: dict, db: Session = Depends(get_db)):
    try:
        task = service.create_task(
            db,
            kind=str(payload.get("kind") or ""),
            model=str(payload.get("model") or ""),
            prompt=str(payload.get("prompt") or ""),
            params=payload.get("params") or {},
            provider=str(payload.get("provider") or ""),
            input_asset_ids=[str(item) for item in (payload.get("input_asset_ids") or [])],
        )
    except service.GenerationError as error:
        raise HTTPException(status_code=error.status_code, detail=_detail({"message": error.message})) from error
    return service.task_to_dict(task, service.task_assets(db, task.id))


@router.get("/tasks")
def list_tasks(
    kind: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    return service.list_tasks(db, kind=kind, status=status, page=page, page_size=page_size)


@router.get("/tasks/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db)):
    task = service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return service.task_to_dict(task, service.task_assets(db, task_id))


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str, db: Session = Depends(get_db)):
    if not service.delete_task(db, task_id):
        raise HTTPException(status_code=404, detail="任务不存在")


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: str, db: Session = Depends(get_db)):
    task = db.get(GenerationTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    task.status = "queued"
    task.progress = 0
    task.error = ""
    task.attempts = 0
    task.upstream_id = ""
    task.finished_at = None
    task.updated_at = utcnow()
    db.flush()
    return service.task_to_dict(task, service.task_assets(db, task_id))


@asset_router.get("/assets/{asset_id}")
def get_asset(asset_id: str, token: str = "", db: Session = Depends(get_db)):
    if media_id_from_token(token) != asset_id:
        raise HTTPException(status_code=401, detail="需要有效的媒体令牌")
    asset = db.get(GenerationAsset, asset_id)
    if asset is None or asset.purged or asset.status != "ready" or not asset.filename:
        raise HTTPException(status_code=404, detail="文件不存在")
    if asset.task_id:
        path: Path = storage.asset_path(asset.task_id, asset.filename)
    else:
        path = storage.pending_dir() / asset.id / asset.filename
    if not path.is_file():
        raise HTTPException(status_code=410, detail="文件已不可用")
    return FileResponse(
        path,
        media_type=asset.content_type or "application/octet-stream",
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )

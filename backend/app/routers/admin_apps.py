from __future__ import annotations

import contextlib
import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_admin
from app.models import UpstreamAccount
from app.schemas import (
    AppBindingAccountOut,
    AppOcrResultOut,
    AppOut,
    AppUpdate,
    StaticSiteCreate,
    StaticSiteOut,
)
from app.services.apps import ocr as ocr_service
from app.services.apps import static_deploy as static_service
from app.services.apps.mcp_tools import mcp_server_info, openai_tools, tool_definitions
from app.services.apps.service import get_app, list_apps, require_enabled_app, update_app
from app.services.key_models import is_account_available
from app.services.model_caps import first_model_id
from app.config import get_settings

router = APIRouter(prefix="/api/admin/apps", tags=["admin-apps"], dependencies=[Depends(get_current_admin)])
public_router = APIRouter(tags=["app-public"])


@router.get("", response_model=list[AppOut])
def admin_list_apps(db: Session = Depends(get_db)):
    return [AppOut(**item) for item in list_apps(db)]


@router.get("/integration")
def admin_integration_guide(db: Session = Depends(get_db)):
    """给控制台展示：MCP 连接、工具列表、Skills 提示。"""
    base = get_settings().app_base_url.rstrip("/")
    tools = tool_definitions(db)
    return {
        "server": mcp_server_info(),
        "mcp_url": f"{base}/mcp",
        "mcp_url_alt": f"{base}/api/mcp",
        "rest_tools_url": f"{base}/api/apps/tools",
        "rest_call_url": f"{base}/api/apps/tools/call",
        "auth_header": "Authorization: Bearer <API_KEY>",
        "cursor_config": {
            "mcpServers": {
                "gateway-app-center": {
                    "url": f"{base}/mcp",
                    "headers": {"Authorization": "Bearer <YOUR_API_KEY>"},
                }
            }
        },
        "tools": tools,
        "openai_tools": openai_tools(db),
        "skill_slugs": [
            "gateway-app-center-mcp",
            "gateway-app-ocr",
            "gateway-app-static-deploy",
        ],
        "notes": [
            "MCP 与 REST 均使用平台 API Key 鉴权（与 /v1 相同）",
            "仅已启用的应用会暴露对应 tools",
            "OCR 需在应用配置中绑定支持视觉的上游账号",
            "Skills 仓库已种子 gateway-app-* ，可在 Skills 页下载安装到 Claude/Cursor",
        ],
    }


@router.get("/binding-accounts", response_model=list[AppBindingAccountOut])
def binding_accounts(db: Session = Depends(get_db)):
    rows = db.scalars(select(UpstreamAccount).order_by(UpstreamAccount.id)).all()
    items: list[AppBindingAccountOut] = []
    for account in rows:
        models: list[str] = []
        raw = account.models_json
        if raw:
            with contextlib.suppress(Exception):
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    for entry in parsed:
                        if isinstance(entry, str):
                            models.append(entry)
                        elif isinstance(entry, dict) and entry.get("id"):
                            models.append(str(entry["id"]))
        items.append(
            AppBindingAccountOut(
                id=account.id,
                name=account.name,
                provider=account.provider,
                source=account.source or "upstream",
                available=is_account_available(account),
                default_model=first_model_id(account.models_json),
                models=models[:200],
            )
        )
    return items


@router.post("/ocr/recognize", response_model=AppOcrResultOut)
async def ocr_recognize(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    manifest, installation = require_enabled_app(db, "ocr")
    payload = await file.read()
    result = await ocr_service.recognize_image(
        db,
        installation=installation,
        manifest=manifest,
        image_bytes=payload,
        filename=file.filename,
        content_type=file.content_type,
    )
    if not result.ok:
        raise HTTPException(400, result.error or "识别失败")
    return AppOcrResultOut(
        ok=True,
        text=result.text,
        model=result.model,
        account_id=result.account_id,
        ms=result.ms,
        error=None,
    )


@router.get("/static-deploy/sites", response_model=list[StaticSiteOut])
def list_static_sites(db: Session = Depends(get_db)):
    require_enabled_app(db, "static-deploy")
    return [StaticSiteOut(**item) for item in static_service.list_sites(db)]


@router.post("/static-deploy/sites", response_model=StaticSiteOut, status_code=201)
def create_static_site(payload: StaticSiteCreate, db: Session = Depends(get_db)):
    result = static_service.create_site(
        db,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
    )
    db.commit()
    return StaticSiteOut(**result)


@router.delete("/static-deploy/sites/{site_id}", status_code=204)
def delete_static_site(site_id: int, db: Session = Depends(get_db)):
    static_service.delete_site(db, site_id)
    db.commit()


@router.post("/static-deploy/sites/{site_id}/upload", response_model=StaticSiteOut)
async def upload_static_site(
    site_id: int,
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    paths: list[str] | None = Form(default=None),
    db: Session = Depends(get_db),
):
    if file is not None and file.filename:
        payload = await file.read()
        name = (file.filename or "").lower()
        if name.endswith(".zip") or (file.content_type or "").endswith("zip"):
            result = static_service.deploy_zip(db, site_id, payload, file.filename)
        else:
            result = static_service.deploy_files(db, site_id, [(file.filename or "index.html", payload)])
        db.commit()
        return StaticSiteOut(**result)

    upload_list = files or []
    if not upload_list:
        raise HTTPException(400, "请上传 zip 或静态文件")
    pairs: list[tuple[str, bytes]] = []
    path_list = paths or []
    for index, item in enumerate(upload_list):
        content = await item.read()
        rel = path_list[index] if index < len(path_list) else (item.filename or f"file-{index}")
        pairs.append((rel, content))
    result = static_service.deploy_files(db, site_id, pairs)
    db.commit()
    return StaticSiteOut(**result)


@router.get("/{app_id}", response_model=AppOut)
def admin_get_app(app_id: str, db: Session = Depends(get_db)):
    return AppOut(**get_app(db, app_id))


@router.patch("/{app_id}", response_model=AppOut)
def admin_update_app(app_id: str, payload: AppUpdate, db: Session = Depends(get_db)):
    fields = payload.model_fields_set
    kwargs: dict[str, Any] = {}
    if "enabled" in fields:
        kwargs["enabled"] = payload.enabled
    if "config" in fields:
        kwargs["config"] = payload.config
    if payload.clear_binding:
        kwargs["bound_account_id"] = None
        kwargs["bound_model"] = None
    else:
        if "bound_account_id" in fields:
            kwargs["bound_account_id"] = payload.bound_account_id
            if payload.bound_account_id is not None:
                account = db.get(UpstreamAccount, payload.bound_account_id)
                if account is None:
                    raise HTTPException(400, "上游账号不存在")
        if "bound_model" in fields:
            kwargs["bound_model"] = payload.bound_model
    result = update_app(db, app_id, **kwargs)
    db.commit()
    return AppOut(**result)


@public_router.api_route("/a/{slug}", methods=["GET", "HEAD"])
@public_router.api_route("/a/{slug}/", methods=["GET", "HEAD"])
def public_static_index(slug: str, db: Session = Depends(get_db)):
    path = static_service.resolve_public_file(db, slug, "")
    return FileResponse(path)


@public_router.api_route("/a/{slug}/{file_path:path}", methods=["GET", "HEAD"])
def public_static_file(slug: str, file_path: str, db: Session = Depends(get_db)):
    path = static_service.resolve_public_file(db, slug, file_path)
    return FileResponse(path)

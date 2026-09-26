from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.capabilities.site import hosting, sites as service
from app.capabilities.site.errors import SiteError
from app.db import get_db
from app.services.mcp_auth import allowed_capability_ids, get_mcp_key_from_headers

router = APIRouter(prefix="/v1/sites", tags=["sites"])
hosting_router = APIRouter(tags=["sites-hosting"])


def _error(status: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"type": error_type, "message": message}})


def _mcp_key_dep(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    return get_mcp_key_from_headers(db, authorization, x_api_key)


def _require_site(mcp_key) -> bool:
    return "site" in allowed_capability_ids(mcp_key)


def _deploy_payload(db: Session, site, version) -> dict:
    return {
        "site": service.site_payload(site, current=service.current_version(db, site)),
        "version": service.version_payload(version, current_version_id=site.current_version_id),
        "preview_url": service.preview_url(site.slug),
    }


@router.post("", status_code=202)
async def create_site(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    slug: str | None = Form(default=None),
    name: str | None = Form(default=None),
    entry: str | None = Form(default=None),
    activate: bool = Form(default=True),
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    if not _require_site(mcp_key):
        return _error(403, "permission_error", "MCP Key 未授权能力 site")
    raw = await file.read()
    await file.close()
    try:
        site, version = service.create_deploy(
            db,
            archive_bytes=raw,
            filename=file.filename or "upload.zip",
            created_by="key",
            mcp_key_id=mcp_key.id,
            slug=slug,
            name=name,
            entry=entry,
            activate=activate,
        )
    except SiteError as error:
        return _error(error.status_code, error.error_type, error.message)
    db.commit()
    background.add_task(service.run_deploy_task, version.id, activate)
    return _deploy_payload(db, site, version)


@router.get("")
def list_sites(db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    if not _require_site(mcp_key):
        return _error(403, "permission_error", "MCP Key 未授权能力 site")
    rows, total = service.list_sites(db, mcp_key_id=mcp_key.id)
    return {
        "items": [service.site_payload(site, current=service.current_version(db, site)) for site in rows],
        "total": total,
    }


@router.get("/{slug}")
def get_site(slug: str, db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    if not _require_site(mcp_key):
        return _error(403, "permission_error", "MCP Key 未授权能力 site")
    try:
        site = service.get_site_by_slug(db, slug, mcp_key.id)
    except SiteError as error:
        return _error(error.status_code, error.error_type, error.message)
    return service.site_detail_payload(db, site)


@router.get("/{slug}/versions/{version_no}")
def get_version(slug: str, version_no: int, db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    if not _require_site(mcp_key):
        return _error(403, "permission_error", "MCP Key 未授权能力 site")
    try:
        site = service.get_site_by_slug(db, slug, mcp_key.id)
        versions = service.list_versions(db, site)
    except SiteError as error:
        return _error(error.status_code, error.error_type, error.message)
    version = next((item for item in versions if item.version_no == version_no), None)
    if version is None:
        return _error(404, "not_found", "版本不存在")
    return service.version_payload(version, current_version_id=site.current_version_id)


@router.post("/{slug}/rollback")
def rollback(
    slug: str,
    payload: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    if not _require_site(mcp_key):
        return _error(403, "permission_error", "MCP Key 未授权能力 site")
    try:
        site = service.get_site_by_slug(db, slug, mcp_key.id)
        version = service.rollback(db, site, int(payload.get("version_no") or 0))
    except SiteError as error:
        return _error(error.status_code, error.error_type, error.message)
    db.commit()
    return {"site": service.site_payload(site, current=version), "version": service.version_payload(version, current_version_id=site.current_version_id)}


@router.post("/{slug}/access")
def set_access(
    slug: str,
    payload: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    if not _require_site(mcp_key):
        return _error(403, "permission_error", "MCP Key 未授权能力 site")
    try:
        site = service.get_site_by_slug(db, slug, mcp_key.id)
        site, token = service.set_access(
            db,
            site,
            mode=str(payload.get("mode") or ""),
            reset_token=bool(payload.get("reset_token")),
        )
    except SiteError as error:
        return _error(error.status_code, error.error_type, error.message)
    db.commit()
    return {"site": service.site_payload(site, current=service.current_version(db, site)), "token": token}


@router.post("/{slug}/token")
def reset_token(slug: str, db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    if not _require_site(mcp_key):
        return _error(403, "permission_error", "MCP Key 未授权能力 site")
    try:
        site = service.get_site_by_slug(db, slug, mcp_key.id)
        token = service.reset_token(db, site)
    except SiteError as error:
        return _error(error.status_code, error.error_type, error.message)
    db.commit()
    return {"site": service.site_payload(site, current=service.current_version(db, site)), "token": token}


@router.delete("/{slug}", status_code=204)
def delete_site(slug: str, db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    if not _require_site(mcp_key):
        return _error(403, "permission_error", "MCP Key 未授权能力 site")
    try:
        site = service.get_site_by_slug(db, slug, mcp_key.id)
        service.delete_site(db, site)
    except SiteError as error:
        return _error(error.status_code, error.error_type, error.message)
    db.commit()


@hosting_router.api_route("/sites/{slug}", methods=["GET", "HEAD"])
def hosting_root(request: Request, slug: str):
    return hosting.redirect_slug(request, slug)


@hosting_router.api_route("/sites/{slug}/{path:path}", methods=["GET", "HEAD"])
def hosting_file(request: Request, slug: str, path: str, db: Session = Depends(get_db)):
    return hosting.serve(db, request, slug, path)

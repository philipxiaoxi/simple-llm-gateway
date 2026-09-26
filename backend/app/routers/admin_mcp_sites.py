from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.capabilities.site import sites as service
from app.capabilities.site.errors import SiteError
from app.db import get_db
from app.deps import get_current_admin
from app.models import SiteVersion

router = APIRouter(
    prefix="/api/admin/mcp/sites",
    tags=["admin-mcp-sites"],
    dependencies=[Depends(get_current_admin)],
)


def _http(error: SiteError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"error": {"type": error.error_type, "message": error.message}},
    )


def _deploy_payload(db: Session, site, version) -> dict:
    return {
        "site": service.site_payload(site, current=service.current_version(db, site)),
        "version": service.version_payload(version, current_version_id=site.current_version_id),
        "preview_url": service.preview_url(site.slug),
    }


def _get_version(db: Session, site, version_id: str) -> SiteVersion:
    version = db.get(SiteVersion, version_id)
    if version is None or version.site_id != site.id:
        raise SiteError("版本不存在", status_code=404, error_type="not_found")
    return version


@router.get("")
def admin_list(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    rows, total = service.list_sites(db, limit=limit, offset=offset, q=q, status=status)
    return {
        "items": [service.site_payload(site, current=service.current_version(db, site)) for site in rows],
        "total": total,
    }


@router.post("", status_code=202)
async def admin_deploy(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    slug: str | None = Form(default=None),
    name: str | None = Form(default=None),
    entry: str | None = Form(default=None),
    activate: bool = Form(default=True),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    await file.close()
    try:
        site, version = service.create_deploy(
            db,
            archive_bytes=raw,
            filename=file.filename or "upload.zip",
            created_by="admin",
            mcp_key_id=None,
            slug=slug,
            name=name,
            entry=entry,
            activate=activate,
        )
    except SiteError as error:
        raise _http(error) from error
    db.commit()
    background.add_task(service.run_deploy_task, version.id, activate)
    return _deploy_payload(db, site, version)


@router.get("/{site_id}")
def admin_detail(site_id: str, db: Session = Depends(get_db)):
    try:
        site = service.get_site(db, site_id, None)
    except SiteError as error:
        raise _http(error) from error
    return service.site_detail_payload(db, site)


@router.get("/{site_id}/versions/{version_id}")
def admin_version(site_id: str, version_id: str, db: Session = Depends(get_db)):
    try:
        site = service.get_site(db, site_id, None)
        version = _get_version(db, site, version_id)
    except SiteError as error:
        raise _http(error) from error
    return service.version_payload(version, current_version_id=site.current_version_id)


@router.patch("/{site_id}")
def admin_update(site_id: str, payload: dict[str, Any] = Body(default={}), db: Session = Depends(get_db)):
    try:
        site = service.get_site(db, site_id, None)
        service.update_site(
            db,
            site,
            name=payload.get("name"),
            description=payload.get("description"),
            access_mode=payload.get("access_mode"),
            entry_file=payload.get("entry_file"),
            spa_fallback=payload.get("spa_fallback"),
            status=payload.get("status"),
        )
    except SiteError as error:
        raise _http(error) from error
    db.commit()
    return service.site_detail_payload(db, site)


@router.delete("/{site_id}", status_code=204)
def admin_delete(site_id: str, db: Session = Depends(get_db)):
    try:
        site = service.get_site(db, site_id, None)
        service.delete_site(db, site)
    except SiteError as error:
        raise _http(error) from error
    db.commit()


@router.post("/{site_id}/versions", status_code=202)
async def admin_new_version(
    site_id: str,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    entry: str | None = Form(default=None),
    activate: bool = Form(default=True),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    await file.close()
    try:
        site = service.get_site(db, site_id, None)
        _target, version = service.create_deploy(
            db,
            archive_bytes=raw,
            filename=file.filename or "upload.zip",
            created_by="admin",
            mcp_key_id=None,
            entry=entry,
            activate=activate,
            site=site,
        )
    except SiteError as error:
        raise _http(error) from error
    db.commit()
    background.add_task(service.run_deploy_task, version.id, activate)
    return _deploy_payload(db, site, version)


@router.post("/{site_id}/versions/{version_id}/activate")
def admin_activate(site_id: str, version_id: str, db: Session = Depends(get_db)):
    try:
        site = service.get_site(db, site_id, None)
        version = _get_version(db, site, version_id)
        service.activate_version(db, site, version)
    except SiteError as error:
        raise _http(error) from error
    db.commit()
    return service.site_detail_payload(db, site)


@router.post("/{site_id}/versions/{version_id}/retry", status_code=202)
def admin_retry(
    site_id: str,
    version_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        site = service.get_site(db, site_id, None)
        version = _get_version(db, site, version_id)
        service.retry(db, site, version)
    except SiteError as error:
        raise _http(error) from error
    db.commit()
    background.add_task(service.run_deploy_task, version.id, True)
    return service.version_payload(version, current_version_id=site.current_version_id)


@router.delete("/{site_id}/versions/{version_id}", status_code=204)
def admin_delete_version(site_id: str, version_id: str, db: Session = Depends(get_db)):
    try:
        site = service.get_site(db, site_id, None)
        version = _get_version(db, site, version_id)
        service.delete_version(db, site, version)
    except SiteError as error:
        raise _http(error) from error
    db.commit()


@router.post("/{site_id}/token")
def admin_token(site_id: str, db: Session = Depends(get_db)):
    try:
        site = service.get_site(db, site_id, None)
        token = service.set_token(db, site)
    except SiteError as error:
        raise _http(error) from error
    db.commit()
    return {"token": token, "site": service.site_payload(site, current=service.current_version(db, site))}

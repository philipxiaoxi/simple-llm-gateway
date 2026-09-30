from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.capabilities.diagram import service
from app.capabilities.diagram.errors import DiagramError
from app.capabilities.site import sites as site_service
from app.capabilities.site.errors import SiteError
from app.db import get_db
from app.deps import get_current_admin
from app.models import Site, SiteVersion

router = APIRouter(
    prefix="/api/admin/mcp/diagrams",
    tags=["admin-mcp-diagrams"],
    dependencies=[Depends(get_current_admin)],
)


def _http(error: DiagramError | SiteError) -> HTTPException:
    detail: dict[str, Any] = {"type": error.error_type, "message": error.message}
    diagnostics = getattr(error, "diagnostics", None)
    if diagnostics:
        detail["diagnostics"] = diagnostics
    return HTTPException(status_code=error.status_code, detail={"error": detail})


def _get_diagram(db: Session, site_id: str) -> Site:
    site = site_service.get_site(db, site_id, None)
    if site.origin != service.ORIGIN:
        raise SiteError("图表不存在", status_code=404, error_type="not_found")
    return site


def _get_version(db: Session, site: Site, version_id: str) -> SiteVersion:
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
    rows, total = service.list_diagrams(db, mcp_key_id=None, limit=limit, offset=offset, q=q, status=status)
    return {
        "items": [
            site_service.site_payload(site, current=site_service.current_version(db, site)) for site in rows
        ],
        "total": total,
    }


@router.post("", status_code=202)
async def admin_create(
    background: BackgroundTasks,
    payload: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
):
    activate = bool(payload.get("activate", True))
    try:
        site, version = await service.render_and_create(
            db,
            mcp_key_id=None,
            diagram_type=payload.get("type"),
            source=payload.get("source"),
            quality=payload.get("quality"),
            slug=(str(payload.get("slug")).strip() if payload.get("slug") else None),
            name=(str(payload.get("name")).strip() if payload.get("name") else None),
            activate=activate,
            created_by="admin",
        )
    except (DiagramError, SiteError) as error:
        raise _http(error) from error
    db.commit()
    background.add_task(site_service.run_deploy_task, version.id, activate)
    return {
        "site": site_service.site_payload(site, current=site_service.current_version(db, site)),
        "version": site_service.version_payload(version, current_version_id=site.current_version_id),
        "preview_url": site_service.preview_url(site.slug),
    }


@router.get("/{site_id}")
def admin_detail(site_id: str, db: Session = Depends(get_db)):
    try:
        site = _get_diagram(db, site_id)
    except SiteError as error:
        raise _http(error) from error
    return site_service.site_detail_payload(db, site)


@router.get("/{site_id}/source")
def admin_source(
    site_id: str,
    version_no: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    try:
        site = _get_diagram(db, site_id)
        return service.get_source(db, site, version_no)
    except DiagramError as error:
        raise _http(error) from error
    except SiteError as error:
        raise _http(error) from error


@router.patch("/{site_id}")
def admin_update(site_id: str, payload: dict[str, Any] = Body(default={}), db: Session = Depends(get_db)):
    try:
        site = _get_diagram(db, site_id)
        fields = {field: payload[field] for field in site_service.EDITABLE_FIELDS if field in payload}
        site_service.update_site(db, site, **fields)
    except (DiagramError, SiteError) as error:
        raise _http(error) from error
    db.commit()
    return site_service.site_detail_payload(db, site)


@router.post("/{site_id}/rollback")
def admin_rollback(site_id: str, payload: dict[str, Any] = Body(default={}), db: Session = Depends(get_db)):
    try:
        site = _get_diagram(db, site_id)
        if payload.get("version_id"):
            version = _get_version(db, site, str(payload["version_id"]))
            site_service.activate_version(db, site, version)
        else:
            version = site_service.rollback(db, site, int(payload.get("version_no") or 0))
    except (DiagramError, SiteError) as error:
        raise _http(error) from error
    db.commit()
    return site_service.site_detail_payload(db, site)


@router.delete("/{site_id}", status_code=204)
def admin_delete(site_id: str, db: Session = Depends(get_db)):
    try:
        site = _get_diagram(db, site_id)
        site_service.delete_site(db, site)
    except SiteError as error:
        raise _http(error) from error
    db.commit()


@router.get("/{site_id}/token")
def admin_reveal_token(site_id: str, db: Session = Depends(get_db)):
    try:
        site = _get_diagram(db, site_id)
    except SiteError as error:
        raise _http(error) from error
    token = site_service.reveal_token(site) if site.access_mode == "token" and site.access_token_hash else None
    return {
        "token": token,
        "url": site_service.access_url(site, token),
        "site": site_service.site_payload(site, current=site_service.current_version(db, site)),
    }


@router.post("/{site_id}/token")
def admin_token(site_id: str, db: Session = Depends(get_db)):
    try:
        site = _get_diagram(db, site_id)
        token = site_service.set_token(db, site)
    except SiteError as error:
        raise _http(error) from error
    db.commit()
    return {
        "token": token,
        "url": site_service.access_url(site, token),
        "site": site_service.site_payload(site, current=site_service.current_version(db, site)),
    }

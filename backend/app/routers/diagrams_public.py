from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.capabilities.diagram import service
from app.capabilities.diagram.errors import DiagramError
from app.capabilities.site import sites as site_service
from app.capabilities.site.errors import SiteError
from app.db import get_db
from app.services.mcp_auth import allowed_capability_ids, get_mcp_key_from_headers
from app.services.mcp_logs import begin_mcp_call

router = APIRouter(prefix="/v1/diagrams", tags=["diagrams"])


def _error(
    status: int,
    error_type: str,
    message: str,
    diagnostics: list[dict] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"type": error_type, "message": message}
    if diagnostics:
        error["diagnostics"] = diagnostics
    return JSONResponse(status_code=status, content={"error": error})


def _mcp_key_dep(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    return get_mcp_key_from_headers(db, authorization, x_api_key)


def _gate(db: Session, mcp_key, operation: str):
    rec = begin_mcp_call(db, mcp_key, "diagram", operation)
    if not _require_diagram(mcp_key):
        rec.failure("MCP Key 未授权能力 diagram")
        return rec, _error(403, "permission_error", "MCP Key 未授权能力 diagram")
    return rec, None


def _require_diagram(mcp_key) -> bool:
    return "diagram" in allowed_capability_ids(mcp_key)


def _deploy_payload(db: Session, site, version) -> dict:
    return {
        "site": site_service.site_payload(site, current=site_service.current_version(db, site)),
        "version": site_service.version_payload(version, current_version_id=site.current_version_id),
        "preview_url": site_service.preview_url(site.slug),
    }


def _owned_site(db: Session, slug: str, mcp_key):
    return service.get_diagram(db, slug, mcp_key.id)


@router.post("", status_code=202)
async def create_diagram(
    background: BackgroundTasks,
    payload: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    rec, denied = _gate(db, mcp_key, "create")
    if denied:
        return denied
    activate = bool(payload.get("activate", True))
    try:
        site, version = await service.render_and_create(
            db,
            mcp_key_id=mcp_key.id,
            diagram_type=payload.get("type"),
            source=payload.get("source"),
            quality=payload.get("quality"),
            slug=(str(payload.get("slug")).strip() if payload.get("slug") else None),
            name=(str(payload.get("name")).strip() if payload.get("name") else None),
            entry=(str(payload.get("entry")).strip() if payload.get("entry") else None),
            activate=activate,
        )
    except DiagramError as error:
        rec.failure(error.message)
        return _error(error.status_code, error.error_type, error.message, error.diagnostics)
    except SiteError as error:
        rec.failure(error.message)
        return _error(error.status_code, error.error_type, error.message)
    rec.success()
    db.commit()
    background.add_task(site_service.run_deploy_task, version.id, activate)
    return _deploy_payload(db, site, version)


@router.get("")
def list_diagrams(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    rec, denied = _gate(db, mcp_key, "list")
    if denied:
        return denied
    rec.success()
    rows, total = service.list_diagrams(db, mcp_key_id=mcp_key.id, limit=limit, offset=offset, q=q, status=status)
    return {
        "items": [
            site_service.site_payload(site, current=site_service.current_version(db, site)) for site in rows
        ],
        "total": total,
    }


@router.get("/{slug}")
def get_diagram(slug: str, db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    rec, denied = _gate(db, mcp_key, "status")
    if denied:
        return denied
    try:
        site = _owned_site(db, slug, mcp_key)
    except DiagramError as error:
        rec.failure(error.message)
        return _error(error.status_code, error.error_type, error.message)
    rec.success()
    return site_service.site_detail_payload(db, site)


@router.get("/{slug}/source")
def get_source(
    slug: str,
    version_no: int | None = Query(default=None),
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    rec, denied = _gate(db, mcp_key, "get_source")
    if denied:
        return denied
    try:
        site = _owned_site(db, slug, mcp_key)
        body = service.get_source(db, site, version_no)
    except DiagramError as error:
        rec.failure(error.message)
        return _error(error.status_code, error.error_type, error.message)
    rec.success()
    return body


@router.patch("/{slug}")
def update_diagram(
    slug: str,
    payload: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    rec, denied = _gate(db, mcp_key, "update")
    if denied:
        return denied
    try:
        site = _owned_site(db, slug, mcp_key)
        fields = {field: payload[field] for field in site_service.EDITABLE_FIELDS if field in payload}
        site_service.update_site(db, site, **fields)
    except (DiagramError, SiteError) as error:
        rec.failure(error.message)
        return _error(error.status_code, error.error_type, error.message)
    rec.success()
    db.commit()
    return site_service.site_detail_payload(db, site)


@router.post("/{slug}/rollback")
def rollback(
    slug: str,
    payload: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    rec, denied = _gate(db, mcp_key, "rollback")
    if denied:
        return denied
    try:
        site = _owned_site(db, slug, mcp_key)
        version = site_service.rollback(db, site, int(payload.get("version_no") or 0))
    except (DiagramError, SiteError) as error:
        rec.failure(error.message)
        return _error(error.status_code, error.error_type, error.message)
    rec.success()
    db.commit()
    return {
        "site": site_service.site_payload(site, current=version),
        "version": site_service.version_payload(version, current_version_id=site.current_version_id),
    }


@router.post("/{slug}/access")
def set_access(
    slug: str,
    payload: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    mcp_key=Depends(_mcp_key_dep),
):
    rec, denied = _gate(db, mcp_key, "access")
    if denied:
        return denied
    try:
        site = _owned_site(db, slug, mcp_key)
        site, token = site_service.set_access(
            db,
            site,
            mode=str(payload.get("mode") or ""),
            reset_token=bool(payload.get("reset_token")),
        )
    except (DiagramError, SiteError) as error:
        rec.failure(error.message)
        return _error(error.status_code, error.error_type, error.message)
    rec.success()
    db.commit()
    return {
        "site": site_service.site_payload(site, current=site_service.current_version(db, site)),
        "token": token,
        "url": site_service.access_url(site, token),
    }


@router.get("/{slug}/access")
def get_access(slug: str, db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    rec, denied = _gate(db, mcp_key, "access_info")
    if denied:
        return denied
    try:
        site = _owned_site(db, slug, mcp_key)
    except DiagramError as error:
        rec.failure(error.message)
        return _error(error.status_code, error.error_type, error.message)
    rec.success()
    return site_service.access_info(db, site)


@router.post("/{slug}/token")
def reset_token(slug: str, db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    rec, denied = _gate(db, mcp_key, "access")
    if denied:
        return denied
    try:
        site = _owned_site(db, slug, mcp_key)
        token = site_service.reset_token(db, site)
    except DiagramError as error:
        rec.failure(error.message)
        return _error(error.status_code, error.error_type, error.message)
    rec.success()
    db.commit()
    return {
        "site": site_service.site_payload(site, current=site_service.current_version(db, site)),
        "token": token,
        "url": site_service.access_url(site, token),
    }


@router.delete("/{slug}", status_code=204)
def delete_diagram(slug: str, db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    rec, denied = _gate(db, mcp_key, "delete")
    if denied:
        return denied
    try:
        site = _owned_site(db, slug, mcp_key)
        site_service.delete_site(db, site)
    except DiagramError as error:
        rec.failure(error.message)
        return _error(error.status_code, error.error_type, error.message)
    rec.success()
    db.commit()

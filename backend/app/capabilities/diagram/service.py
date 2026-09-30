from __future__ import annotations

import io
import json
import zipfile
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.capabilities.site import sites as site_service
from app.capabilities.site.errors import SiteError
from app.models import Site, SiteVersion

from . import renderer
from .errors import DiagramError

# 站点判别值：图表由 diagram 能力创建，普通站点为 upload。
ORIGIN = "diagram"


def _zip_single_html(html: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", html)
    return buffer.getvalue()


def _assert_diagram(site: Site) -> Site:
    if site.origin != ORIGIN:
        raise DiagramError("图表不存在", status_code=404, error_type="not_found")
    return site


def get_diagram(db: Session, slug: str, mcp_key_id: int | None) -> Site:
    try:
        site = site_service.get_site_by_slug(db, slug, mcp_key_id)
    except SiteError as error:
        raise DiagramError(error.message, status_code=error.status_code, error_type=error.error_type) from error
    return _assert_diagram(site)


def list_diagrams(
    db: Session,
    *,
    mcp_key_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    status: str | None = None,
) -> tuple[list[Site], int]:
    return site_service.list_sites(
        db, mcp_key_id=mcp_key_id, limit=limit, offset=offset, q=q, status=status, origin=ORIGIN
    )


def coerce_source(value: Any) -> Any:
    """MCP 传入的 source 可能是对象，也可能是 JSON 字符串。"""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise DiagramError("source 不能为空")
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise DiagramError("source 不是合法 JSON") from error
    if isinstance(value, (dict, list)):
        return value
    raise DiagramError("source 必须是 JSON 对象或字符串")


async def render_and_create(
    db: Session,
    *,
    mcp_key_id: int | None,
    diagram_type: Any,
    source: Any,
    quality: Any = None,
    slug: str | None = None,
    name: str | None = None,
    entry: str | None = None,
    activate: bool = True,
    created_by: str = "key",
) -> tuple[Site, SiteVersion]:
    """渲染源并落盘为一个新版本；不执行 deploy（调用方决定同步或后台）。"""
    site_type = renderer.normalize_type(diagram_type)
    normalized_quality = renderer.normalize_quality(quality)
    parsed = coerce_source(source)

    if slug:
        existing = db.scalar(select(Site).where(Site.slug == slug.strip().lower()))
        if existing is not None and existing.origin != ORIGIN:
            raise DiagramError("slug 已被占用", status_code=409, error_type="conflict")

    outcome = await renderer.render_diagram(site_type, parsed, normalized_quality)
    archive = _zip_single_html(outcome.html)
    source_text = json.dumps(parsed, ensure_ascii=False)

    return site_service.create_deploy(
        db,
        archive_bytes=archive,
        filename="diagram.zip",
        created_by=created_by,
        mcp_key_id=mcp_key_id,
        slug=(slug.strip().lower() if slug else None),
        name=(name.strip() if name else None),
        entry="index.html",
        activate=activate,
        origin=ORIGIN,
        diagram_type=site_type,
        source_json=source_text,
        quality=normalized_quality,
    )


async def create_diagram(
    db: Session,
    *,
    mcp_key_id: int | None,
    diagram_type: Any,
    source: Any,
    quality: Any = None,
    slug: str | None = None,
    name: str | None = None,
    entry: str | None = None,
    activate: bool = True,
    created_by: str = "key",
) -> dict[str, Any]:
    site, version = await render_and_create(
        db,
        mcp_key_id=mcp_key_id,
        diagram_type=diagram_type,
        source=source,
        quality=quality,
        slug=slug,
        name=name,
        entry=entry,
        activate=activate,
        created_by=created_by,
    )
    site_service.run_deploy(db, version, activate=activate)
    return _deploy_payload(db, site, version)


def _deploy_payload(db: Session, site: Site, version: SiteVersion) -> dict[str, Any]:
    return {
        "site": site_service.site_payload(site, current=site_service.current_version(db, site)),
        "version": site_service.version_payload(version, current_version_id=site.current_version_id),
        "preview_url": site_service.preview_url(site.slug),
    }


def get_source(db: Session, site: Site, version_no: int | None = None) -> dict[str, Any]:
    version: SiteVersion | None
    if version_no is None:
        version = site_service.current_version(db, site)
    else:
        version = db.scalar(
            select(SiteVersion).where(
                SiteVersion.site_id == site.id, SiteVersion.version_no == int(version_no)
            )
        )
    if version is None:
        raise DiagramError("版本不存在", status_code=404, error_type="not_found")
    source: Any = None
    if version.source_json:
        try:
            source = json.loads(version.source_json)
        except json.JSONDecodeError:
            source = None
    return {
        "slug": site.slug,
        "version_no": version.version_no,
        "type": version.diagram_type,
        "quality": version.quality,
        "source": source,
    }

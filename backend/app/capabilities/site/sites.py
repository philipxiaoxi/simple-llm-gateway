from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import shutil
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.crypto import encrypt_secret
from app.models import Site, SiteVersion

from . import archive, storage
from .errors import SiteError

SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
TERMINAL_STATUSES = ("ready", "failed", "duplicate")


def new_id() -> str:
    from uuid import uuid4

    return str(uuid4())


def slugify(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = text[:48].strip("-")
    return text or "site"


def ensure_unique_slug(db: Session, base: str) -> str:
    candidate = base
    suffix = 2
    while db.scalar(select(Site.id).where(Site.slug == candidate)) is not None:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def preview_url(slug: str) -> str:
    base = get_settings().app_base_url.rstrip("/")
    return f"{base}/sites/{slug}/"


def _assert_owner(site: Site, mcp_key_id: int | None) -> None:
    if mcp_key_id is None:
        return
    if site.mcp_key_id != mcp_key_id:
        raise SiteError("站点不存在", status_code=404, error_type="not_found")


def get_site(db: Session, site_id: str, mcp_key_id: int | None) -> Site:
    site = db.get(Site, site_id)
    if site is None:
        raise SiteError("站点不存在", status_code=404, error_type="not_found")
    _assert_owner(site, mcp_key_id)
    return site


def get_site_by_slug(db: Session, slug: str, mcp_key_id: int | None) -> Site:
    site = db.scalar(select(Site).where(Site.slug == slug))
    if site is None:
        raise SiteError("站点不存在", status_code=404, error_type="not_found")
    _assert_owner(site, mcp_key_id)
    return site


def current_version(db: Session, site: Site) -> SiteVersion | None:
    if not site.current_version_id:
        return None
    return db.get(SiteVersion, site.current_version_id)


def list_sites(
    db: Session,
    *,
    mcp_key_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    status: str | None = None,
) -> tuple[list[Site], int]:
    filters = []
    if mcp_key_id is not None:
        filters.append(Site.mcp_key_id == mcp_key_id)
    if status:
        filters.append(Site.status == status)
    if q and q.strip():
        like = f"%{q.strip()}%"
        filters.append(Site.slug.like(like) | Site.name.like(like))
    total = int(db.scalar(select(func.count()).select_from(Site).where(*filters)) or 0)
    rows = list(
        db.scalars(
            select(Site).where(*filters).order_by(Site.created_at.desc()).offset(offset).limit(limit)
        ).all()
    )
    return rows, total


def list_versions(db: Session, site: Site) -> list[SiteVersion]:
    return list(
        db.scalars(
            select(SiteVersion)
            .where(SiteVersion.site_id == site.id)
            .order_by(SiteVersion.version_no.desc())
        ).all()
    )


def site_payload(site: Site, *, current: SiteVersion | None = None) -> dict:
    return {
        "id": site.id,
        "slug": site.slug,
        "name": site.name,
        "description": site.description,
        "access_mode": site.access_mode,
        "has_token": bool(site.access_token_hash),
        "status": site.status,
        "entry_file": site.entry_file,
        "spa_fallback": bool(site.spa_fallback),
        "current_version_id": site.current_version_id,
        "current_version_no": current.version_no if current else None,
        "preview_url": preview_url(site.slug),
        "total_bytes": site.total_bytes,
        "created_by": site.created_by,
        "mcp_key_id": site.mcp_key_id,
        "created_at": site.created_at.isoformat() if site.created_at else None,
        "updated_at": site.updated_at.isoformat() if site.updated_at else None,
    }


def version_payload(version: SiteVersion, *, current_version_id: str | None = None) -> dict:
    return {
        "id": version.id,
        "site_id": version.site_id,
        "version_no": version.version_no,
        "status": version.status,
        "stage": version.stage,
        "percent": version.percent,
        "message": version.message,
        "content_hash": version.content_hash,
        "reused_version_id": version.reused_version_id,
        "entry_file": version.entry_file,
        "file_count": version.file_count,
        "total_bytes": version.total_bytes,
        "source_name": version.source_name,
        "error_message": version.error_message,
        "created_by": version.created_by,
        "is_current": bool(current_version_id and version.id == current_version_id),
        "purged": bool(version.purged),
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "started_at": version.started_at.isoformat() if version.started_at else None,
        "finished_at": version.finished_at.isoformat() if version.finished_at else None,
    }


def site_detail_payload(db: Session, site: Site) -> dict:
    versions = list_versions(db, site)
    current = next((item for item in versions if item.id == site.current_version_id), None)
    body = site_payload(site, current=current)
    body["versions"] = [version_payload(item, current_version_id=site.current_version_id) for item in versions]
    return body


def _validate_archive(filename: str, raw: bytes) -> None:
    settings = get_settings()
    if not (filename or "").lower().endswith(".zip"):
        raise SiteError("只接受 .zip 归档")
    if len(raw) > settings.site_max_archive_bytes:
        raise SiteError(f"归档超过上限 {settings.site_max_archive_bytes} 字节")
    if not raw:
        raise SiteError("归档为空")


def _resolve_site_for_deploy(
    db: Session,
    *,
    slug: str | None,
    name: str | None,
    mcp_key_id: int | None,
    created_by: str,
    site: Site | None,
) -> Site:
    if site is not None:
        return site
    if slug:
        cleaned = slug.strip().lower()
        if not SLUG_PATTERN.match(cleaned):
            raise SiteError("slug 只允许小写字母、数字与中划线，长度不超过 64")
        existing = db.scalar(select(Site).where(Site.slug == cleaned))
        if existing is not None:
            try:
                _assert_owner(existing, mcp_key_id)
            except SiteError as error:
                raise SiteError("slug 已被占用", status_code=409, error_type="conflict") from error
            return existing
        candidate = cleaned
    else:
        candidate = ensure_unique_slug(db, slugify(name or "site"))
    created = Site(
        id=new_id(),
        slug=candidate,
        name=(name or candidate).strip()[:128],
        access_mode="public",
        current_version_id=None,
        entry_file=get_settings().site_default_entry,
        status="active",
        created_by=created_by,
        mcp_key_id=mcp_key_id,
    )
    db.add(created)
    db.flush()
    return created


def create_deploy(
    db: Session,
    *,
    archive_bytes: bytes,
    filename: str,
    created_by: str,
    mcp_key_id: int | None,
    slug: str | None = None,
    name: str | None = None,
    entry: str | None = None,
    activate: bool = True,
    site: Site | None = None,
) -> tuple[Site, SiteVersion]:
    _validate_archive(filename, archive_bytes)
    target = _resolve_site_for_deploy(
        db, slug=slug, name=name, mcp_key_id=mcp_key_id, created_by=created_by, site=site
    )
    next_no = int(
        db.scalar(select(func.max(SiteVersion.version_no)).where(SiteVersion.site_id == target.id)) or 0
    ) + 1
    version = SiteVersion(
        id=new_id(),
        site_id=target.id,
        version_no=next_no,
        status="unpacking",
        stage="stored",
        percent=0,
        message="已接收，等待解包",
        entry_file=(entry or "").strip().lstrip("/")[:128],
        source_name=(Path(filename).name or "upload.zip")[:256],
        created_by=created_by,
        mcp_key_id=mcp_key_id,
    )
    db.add(version)
    db.flush()
    storage.write_upload(version.id, archive_bytes)
    db.flush()
    return target, version


def _cleanup_failed_files(version: SiteVersion) -> None:
    shutil.rmtree(storage.root() / ".tmp" / version.id, ignore_errors=True)
    storage.remove_upload(version.id)


def run_deploy(db: Session, version: SiteVersion, *, activate: bool = True) -> SiteVersion:
    if version.status != "unpacking":
        return version
    site = db.get(Site, version.site_id)
    if site is None:
        return version
    upload = storage.upload_path(version.id)
    if not upload.is_file():
        version.status = "failed"
        version.stage = "done"
        version.percent = 100
        version.message = "部署失败"
        version.error_message = "上传归档已丢失，请重新上传"
        version.finished_at = utcnow()
        version.updated_at = utcnow()
        db.flush()
        return version

    version.stage = "extracting"
    version.percent = 20
    version.message = "解包中"
    version.started_at = utcnow()
    version.updated_at = utcnow()
    db.flush()

    work_dir = storage.tmp_dir(version.id)
    try:
        result = archive.extract(upload, work_dir, version.entry_file or None)
    except SiteError as error:
        _cleanup_failed_files(version)
        version.status = "failed"
        version.stage = "done"
        version.percent = 100
        version.message = "部署失败"
        version.error_message = error.message[:2000]
        version.finished_at = utcnow()
        version.updated_at = utcnow()
        db.flush()
        return version

    version.stage = "finalizing"
    version.percent = 80
    version.message = "写入版本"
    db.flush()

    duplicate = db.scalar(
        select(SiteVersion)
        .where(
            SiteVersion.site_id == site.id,
            SiteVersion.content_hash == result.content_hash,
            SiteVersion.status == "ready",
            SiteVersion.id != version.id,
        )
        .order_by(SiteVersion.version_no.desc())
    )

    if duplicate is not None:
        shutil.rmtree(work_dir, ignore_errors=True)
        storage.remove_upload(version.id)
        version.status = "duplicate"
        version.reused_version_id = duplicate.id
        version.content_hash = result.content_hash
        version.entry_file = result.entry_file
        version.file_count = result.file_count
        version.total_bytes = result.total_bytes
        version.percent = 100
        version.message = f"内容与 v{duplicate.version_no} 相同，已复用"
        version.finished_at = utcnow()
        version.updated_at = utcnow()
        if activate:
            site.current_version_id = duplicate.id
            site.total_bytes = duplicate.total_bytes
            site.updated_at = utcnow()
        db.flush()
        return version

    storage.commit(version.id, site.id, version.version_no)
    storage.remove_upload(version.id)
    version.status = "ready"
    version.content_hash = result.content_hash
    version.entry_file = result.entry_file
    version.file_count = result.file_count
    version.total_bytes = result.total_bytes
    version.stage = "done"
    version.percent = 100
    version.message = "部署完成"
    version.error_message = None
    version.finished_at = utcnow()
    version.updated_at = utcnow()
    if activate:
        site.current_version_id = version.id
        site.total_bytes = version.total_bytes
        site.updated_at = utcnow()
    db.flush()
    return version


def run_deploy_task(version_id: str, activate: bool = True) -> None:
    """BackgroundTasks 入口：使用独立 session 执行解包，不依赖请求生命周期。"""
    from app.db import get_session_factory

    session = get_session_factory()()
    try:
        version = session.get(SiteVersion, version_id)
        if version is None:
            return
        try:
            run_deploy(session, version, activate=activate)
        except Exception as error:  # noqa: BLE001
            version.status = "failed"
            version.stage = "done"
            version.percent = 100
            version.message = "部署失败"
            version.error_message = str(error)[:2000]
            version.finished_at = utcnow()
            version.updated_at = utcnow()
        session.commit()
    finally:
        session.close()


def activate_version(db: Session, site: Site, version: SiteVersion) -> SiteVersion:
    if version.site_id != site.id or version.status != "ready" or version.purged:
        raise SiteError("只有已就绪且未清理的版本可以设为当前版本")
    site.current_version_id = version.id
    site.total_bytes = version.total_bytes
    site.updated_at = utcnow()
    db.flush()
    return version


def rollback(db: Session, site: Site, version_no: int) -> SiteVersion:
    version = db.scalar(
        select(SiteVersion).where(SiteVersion.site_id == site.id, SiteVersion.version_no == version_no)
    )
    if version is None:
        raise SiteError("版本不存在", status_code=404, error_type="not_found")
    return activate_version(db, site, version)


def retry(db: Session, site: Site, version: SiteVersion) -> SiteVersion:
    if version.site_id != site.id:
        raise SiteError("版本不存在", status_code=404, error_type="not_found")
    if version.status != "failed":
        raise SiteError("只有失败的版本可以重试")
    if not storage.upload_path(version.id).is_file():
        raise SiteError("上传归档已清理，无法重试")
    version.status = "unpacking"
    version.stage = "stored"
    version.percent = 0
    version.message = "已重新入队"
    version.error_message = None
    version.reused_version_id = None
    version.finished_at = None
    version.updated_at = utcnow()
    db.flush()
    return version


def delete_version(db: Session, site: Site, version: SiteVersion) -> None:
    if version.site_id != site.id:
        raise SiteError("版本不存在", status_code=404, error_type="not_found")
    if version.id == site.current_version_id:
        raise SiteError("当前版本不能删除", status_code=409, error_type="conflict")
    if version.status == "unpacking":
        raise SiteError("正在部署的版本不能删除", status_code=409, error_type="conflict")
    storage.purge_version(site.id, version.version_no)
    storage.remove_upload(version.id)
    db.delete(version)
    db.flush()


def delete_site(db: Session, site: Site) -> None:
    storage.purge_site(site.id)
    db.delete(site)
    db.flush()


def update_site(
    db: Session,
    site: Site,
    *,
    name: str | None = None,
    description: str | None = None,
    access_mode: str | None = None,
    entry_file: str | None = None,
    spa_fallback: bool | None = None,
    status: str | None = None,
) -> Site:
    if name is not None:
        site.name = name.strip()[:128]
    if description is not None:
        site.description = description
    if access_mode is not None:
        if access_mode not in {"public", "token"}:
            raise SiteError("access_mode 只能是 public 或 token")
        site.access_mode = access_mode
    if entry_file is not None:
        site.entry_file = entry_file.strip().lstrip("/")[:128] or get_settings().site_default_entry
    if spa_fallback is not None:
        site.spa_fallback = bool(spa_fallback)
    if status is not None:
        if status not in {"active", "disabled"}:
            raise SiteError("status 只能是 active 或 disabled")
        site.status = status
    site.updated_at = utcnow()
    db.flush()
    return site


def generate_token() -> str:
    return "st-" + secrets.token_urlsafe(24)


def set_token(db: Session, site: Site) -> str:
    token = generate_token()
    site.access_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    site.access_token_encrypted = encrypt_secret(token, get_settings().app_secret_key)
    site.access_mode = "token"
    site.updated_at = utcnow()
    db.flush()
    return token


def set_access(db: Session, site: Site, *, mode: str, reset_token: bool = False) -> tuple[Site, str | None]:
    """设置访问模式；token 模式下按需生成或重置令牌，返回一次性明文。"""
    if mode not in {"public", "token"}:
        raise SiteError("mode 只能是 public 或 token")
    if mode == "token" and (reset_token or not site.access_token_hash):
        token = set_token(db, site)
        return site, token
    site.access_mode = mode
    site.updated_at = utcnow()
    db.flush()
    return site, None


def reset_token(db: Session, site: Site) -> str:
    return set_token(db, site)


def reveal_token(site: Site) -> str | None:
    if not site.access_token_encrypted:
        return None
    from app.crypto import decrypt_secret

    try:
        return decrypt_secret(site.access_token_encrypted, get_settings().app_secret_key)
    except ValueError:
        return None


def verify_token(site: Site, token: str) -> bool:
    stored = site.access_token_hash or ""
    if not stored or not token:
        return False
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, stored)


def gate_cookie_name(site: Site) -> str:
    return f"site_gate_{site.id}"


def gate_cookie_value(site: Site) -> str:
    secret = get_settings().app_secret_key.encode("utf-8")
    message = f"{site.id}:{site.access_token_hash or ''}".encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def cleanup_expired(db: Session) -> int:
    settings = get_settings()
    max_versions = max(1, int(settings.site_max_versions))
    days = max(0, int(settings.site_retention_days))
    cutoff = utcnow() - timedelta(days=days) if days else None
    removed = 0
    sites = db.scalars(select(Site)).all()
    for site in sites:
        versions = db.scalars(
            select(SiteVersion)
            .where(SiteVersion.site_id == site.id)
            .order_by(SiteVersion.version_no.desc())
        ).all()
        for index, version in enumerate(versions):
            if version.id == site.current_version_id or version.purged:
                continue
            if version.status not in ("ready", "duplicate"):
                continue
            too_many = index >= max_versions
            too_old = bool(cutoff and version.created_at and version.created_at < cutoff)
            if not (too_many or too_old):
                continue
            if version.status == "ready":
                storage.purge_version(site.id, version.version_no)
            storage.remove_upload(version.id)
            version.purged = 1
            removed += 1
    if removed:
        db.flush()
    return removed


def reconcile_stuck(db: Session) -> int:
    rows = db.scalars(select(SiteVersion).where(SiteVersion.status == "unpacking")).all()
    changed = 0
    for version in rows:
        _cleanup_failed_files(version)
        version.status = "failed"
        version.stage = "done"
        version.percent = 100
        version.message = "部署失败"
        version.error_message = "进程重启，请重试"
        version.finished_at = utcnow()
        version.updated_at = utcnow()
        changed += 1
    if changed:
        db.flush()
    return changed

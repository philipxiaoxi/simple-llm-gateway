from __future__ import annotations

import io
import re
import shutil
import zipfile
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.models import AppInstallation, StaticSite
from app.services.apps.registry import get_manifest
from app.services.apps.service import installation_config, require_enabled_app

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SAFE_REL = re.compile(r"^(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$")


def site_root(site_id: int) -> Path:
    path = get_settings().resolved_apps_path / "static-deploy" / str(site_id)
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def public_path(slug: str) -> str:
    return f"/a/{slug}/"


def _limits(db: Session) -> tuple[int, int]:
    settings = get_settings()
    manifest = get_manifest("static-deploy")
    installation = db.scalar(select(AppInstallation).where(AppInstallation.app_id == "static-deploy"))
    max_sites = settings.apps_static_max_sites
    max_bytes = settings.apps_static_max_site_bytes
    if manifest and installation:
        config = installation_config(installation, manifest)
        try:
            max_sites = int(config.get("max_sites") or max_sites)
        except (TypeError, ValueError):
            pass
        try:
            max_bytes = int(config.get("max_site_bytes") or max_bytes)
        except (TypeError, ValueError):
            pass
    return max(1, max_sites), max(1024 * 1024, max_bytes)


def validate_slug(slug: str) -> str:
    value = (slug or "").strip().lower()
    if not _SLUG_RE.fullmatch(value):
        raise HTTPException(400, "slug 仅允许小写字母、数字与短横线，且以字母或数字开头，最长 63 位")
    return value


def site_to_dict(site: StaticSite) -> dict:
    return {
        "id": site.id,
        "name": site.name,
        "slug": site.slug,
        "description": site.description,
        "entry_file": site.entry_file,
        "file_count": site.file_count,
        "total_bytes": site.total_bytes,
        "status": site.status,
        "error_message": site.error_message,
        "public_url": public_path(site.slug),
        "created_at": site.created_at.isoformat() if site.created_at else None,
        "updated_at": site.updated_at.isoformat() if site.updated_at else None,
    }


def list_sites(db: Session) -> list[dict]:
    rows = db.scalars(select(StaticSite).order_by(StaticSite.id.desc())).all()
    return [site_to_dict(row) for row in rows]


def get_site(db: Session, site_id: int) -> StaticSite:
    site = db.get(StaticSite, site_id)
    if site is None:
        raise HTTPException(404, "站点不存在")
    return site


def create_site(db: Session, *, name: str, slug: str, description: str | None = None) -> dict:
    require_enabled_app(db, "static-deploy")
    max_sites, _ = _limits(db)
    count = db.scalar(select(func.count()).select_from(StaticSite)) or 0
    if int(count) >= max_sites:
        raise HTTPException(400, f"站点数量已达上限（{max_sites}）")

    clean_name = (name or "").strip()
    if not clean_name:
        raise HTTPException(400, "站点名称不能为空")
    clean_slug = validate_slug(slug)
    if db.scalar(select(StaticSite).where(StaticSite.slug == clean_slug)):
        raise HTTPException(409, "slug 已被占用")

    site = StaticSite(
        name=clean_name,
        slug=clean_slug,
        description=(description or "").strip() or None,
        status="empty",
    )
    db.add(site)
    db.flush()
    site_root(site.id)
    return site_to_dict(site)


def delete_site(db: Session, site_id: int) -> None:
    require_enabled_app(db, "static-deploy")
    site = get_site(db, site_id)
    root = site_root(site.id)
    db.delete(site)
    db.flush()
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


def _safe_member_path(name: str) -> str | None:
    normalized = name.replace("\\", "/").lstrip("/")
    if not normalized or normalized.endswith("/"):
        return None
    if ".." in normalized.split("/"):
        return None
    if normalized.startswith("__MACOSX/") or "/__MACOSX/" in normalized:
        return None
    if not _SAFE_REL.fullmatch(normalized):
        return None
    return normalized


def _count_tree(root: Path) -> tuple[int, int]:
    files = 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            files += 1
            total += path.stat().st_size
    return files, total


def _pick_entry(root: Path) -> str:
    if (root / "index.html").is_file():
        return "index.html"
    html_files = sorted(path.relative_to(root).as_posix() for path in root.rglob("*.html") if path.is_file())
    return html_files[0] if html_files else "index.html"


def _clear_dir(root: Path) -> None:
    if root.exists():
        for child in root.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    root.mkdir(parents=True, exist_ok=True)


def deploy_zip(db: Session, site_id: int, payload: bytes, filename: str | None = None) -> dict:
    require_enabled_app(db, "static-deploy")
    site = get_site(db, site_id)
    _, max_bytes = _limits(db)
    if not payload:
        raise HTTPException(400, "上传内容为空")
    if len(payload) > max_bytes:
        raise HTTPException(400, f"压缩包超过单站体积上限（{max_bytes} 字节）")

    root = site_root(site.id)
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members: list[tuple[str, zipfile.ZipInfo]] = []
            total = 0
            for info in archive.infolist():
                if info.is_dir():
                    continue
                rel = _safe_member_path(info.filename)
                if rel is None:
                    continue
                total += int(info.file_size)
                if total > max_bytes:
                    raise HTTPException(400, f"解压后体积超过单站上限（{max_bytes} 字节）")
                members.append((rel, info))
            if not members:
                raise HTTPException(400, "压缩包中没有可用文件")

            _clear_dir(root)
            for rel, info in members:
                target = (root / rel).resolve()
                if not target.is_relative_to(root):
                    raise HTTPException(400, "压缩包包含非法路径")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as dest:
                    shutil.copyfileobj(source, dest)
    except zipfile.BadZipFile as error:
        site.status = "failed"
        site.error_message = "无效的 zip 文件"
        site.updated_at = utcnow()
        db.add(site)
        db.flush()
        raise HTTPException(400, "无效的 zip 文件") from error
    except HTTPException:
        raise
    except Exception as exc:
        site.status = "failed"
        site.error_message = f"{type(exc).__name__}: {exc}"
        site.updated_at = utcnow()
        db.add(site)
        db.flush()
        raise HTTPException(400, f"部署失败：{exc}") from exc

    files, total_bytes = _count_tree(root)
    site.file_count = files
    site.total_bytes = total_bytes
    site.entry_file = _pick_entry(root)
    site.status = "ready"
    site.error_message = None
    site.updated_at = utcnow()
    db.add(site)
    db.flush()
    _ = filename
    return site_to_dict(site)


def deploy_files(db: Session, site_id: int, files: list[tuple[str, bytes]]) -> dict:
    require_enabled_app(db, "static-deploy")
    site = get_site(db, site_id)
    _, max_bytes = _limits(db)
    if not files:
        raise HTTPException(400, "请至少上传一个文件")

    prepared: list[tuple[str, bytes]] = []
    total = 0
    for name, content in files:
        rel = _safe_member_path(name) or _safe_member_path(Path(name).name)
        if rel is None:
            raise HTTPException(400, f"非法文件名：{name}")
        total += len(content)
        if total > max_bytes:
            raise HTTPException(400, f"文件总体积超过单站上限（{max_bytes} 字节）")
        prepared.append((rel, content))

    root = site_root(site.id)
    _clear_dir(root)
    for rel, content in prepared:
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            raise HTTPException(400, "非法路径")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    files_count, total_bytes = _count_tree(root)
    site.file_count = files_count
    site.total_bytes = total_bytes
    site.entry_file = _pick_entry(root)
    site.status = "ready"
    site.error_message = None
    site.updated_at = utcnow()
    db.add(site)
    db.flush()
    return site_to_dict(site)


def resolve_public_file(db: Session, slug: str, rel_path: str) -> Path:
    clean_slug = (slug or "").strip().lower()
    site = db.scalar(select(StaticSite).where(StaticSite.slug == clean_slug))
    if site is None or site.status != "ready":
        raise HTTPException(404, "站点不存在或尚未部署")

    rel = (rel_path or "").strip().lstrip("/")
    if not rel or rel.endswith("/"):
        rel = site.entry_file or "index.html"
    safe = _safe_member_path(rel)
    if safe is None:
        raise HTTPException(404, "文件不存在")

    root = site_root(site.id)
    target = (root / safe).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "文件不存在")
    return target

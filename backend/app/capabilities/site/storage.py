from __future__ import annotations

import shutil
import time
from pathlib import Path

from app.config import get_settings


def root() -> Path:
    return get_settings().resolved_site_deploy_path


def upload_path(version_id: str) -> Path:
    directory = root() / ".upload"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{version_id}.zip"


def write_upload(version_id: str, raw: bytes) -> Path:
    path = upload_path(version_id)
    path.write_bytes(raw)
    return path


def tmp_dir(version_id: str) -> Path:
    path = root() / ".tmp" / version_id
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def version_dir(site_id: str, version_no: int) -> Path:
    return root() / site_id / f"v{version_no}"


def commit(version_id: str, site_id: str, version_no: int) -> Path:
    """把临时目录原子移动到最终版本目录。同文件系统内 rename 即生效。"""
    target = version_dir(site_id, version_no)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    source = root() / ".tmp" / version_id
    source.rename(target)
    return target


def purge_version(site_id: str, version_no: int) -> None:
    path = version_dir(site_id, version_no)
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def purge_site(site_id: str) -> None:
    path = root() / site_id
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def remove_upload(version_id: str) -> None:
    path = upload_path(version_id)
    if path.is_file():
        path.unlink(missing_ok=True)


def cleanup_temp(max_age_seconds: int = 24 * 3600) -> int:
    cutoff = time.time() - max_age_seconds
    removed = 0
    for name in (".upload", ".tmp"):
        directory = root() / name
        if not directory.is_dir():
            continue
        for child in directory.iterdir():
            try:
                if child.stat().st_mtime >= cutoff:
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
    return removed

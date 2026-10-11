"""生成媒体（参考文件与作品结果）的本地存储。

目录结构：`{GENERATION_PATH}/{task_id}/{filename}`。参考文件在任务创建前上传，
先落到一个临时归属（`pending`），创建任务后再回填 task_id 并移动到任务目录。
"""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
import time
from contextlib import suppress
from pathlib import Path

from app.config import get_settings

PENDING_DIR = "_pending"


def root() -> Path:
    return get_settings().resolved_generation_path


def task_dir(task_id: str) -> Path:
    path = root() / task_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def pending_dir() -> Path:
    path = root() / PENDING_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def asset_path(task_id: str, filename: str) -> Path:
    safe = Path(filename).name
    return task_dir(task_id) / safe


def store_bytes(task_id: str, filename: str, data: bytes) -> tuple[Path, str, int]:
    """写入文件并返回 (路径, sha256, 字节数)。"""
    path = asset_path(task_id, filename)
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest(), len(data)


def guess_extension(content_type: str, kind: str) -> str:
    ext = mimetypes.guess_extension((content_type or "").split(";")[0].strip()) or ""
    if ext == ".jpe":
        ext = ".jpg"
    if not ext:
        ext = {"image": ".png", "video": ".mp4", "audio": ".mp3"}.get(kind, ".bin")
    return ext


def purge_task(task_id: str) -> None:
    for base in (root() / task_id, pending_dir() / task_id):
        if base.is_dir():
            shutil.rmtree(base, ignore_errors=True)


def cleanup_orphan_pending(max_age_seconds: int = 24 * 3600) -> int:
    """清理上传后一直没提交任务的参考文件目录。"""
    base = root() / PENDING_DIR
    if not base.is_dir():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for child in base.iterdir():
        with suppress(OSError):
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
    return removed

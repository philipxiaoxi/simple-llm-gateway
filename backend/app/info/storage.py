from __future__ import annotations

import shutil
import time
from pathlib import Path

from app.config import get_settings


def root() -> Path:
    return get_settings().resolved_info_media_path


def item_dir(item_id: str) -> Path:
    path = root() / item_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def media_path(item_id: str, filename: str) -> Path:
    # 文件名由系统生成，这里再做一次兜底，禁止任何路径分隔符。
    safe = Path(filename).name
    return item_dir(item_id) / safe


def purge_item(item_id: str) -> None:
    path = root() / item_id
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def cleanup_temp(max_age_seconds: int = 24 * 3600) -> int:
    """清理历史遗留的 .part 半成品（异常退出、超时中断留下）。"""
    cutoff = time.time() - max_age_seconds
    removed = 0
    base = root()
    if not base.is_dir():
        return 0
    for child in base.rglob("*.part"):
        try:
            if child.stat().st_mtime >= cutoff:
                continue
            child.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed

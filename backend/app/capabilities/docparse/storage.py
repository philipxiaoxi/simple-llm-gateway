from __future__ import annotations

import shutil
from pathlib import Path

from app.config import get_settings


def job_dir(job_id: str) -> Path:
    root = get_settings().resolved_docparse_path
    path = root / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_source(job_id: str, ext: str, raw: bytes) -> Path:
    safe_ext = ext if ext.startswith(".") and len(ext) <= 16 else ".bin"
    path = job_dir(job_id) / f"source{safe_ext}"
    path.write_bytes(raw)
    return path


def write_markdown(job_id: str, markdown: str) -> Path:
    path = job_dir(job_id) / "result.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def read_markdown(job_id: str) -> str:
    path = get_settings().resolved_docparse_path / job_id / "result.md"
    return path.read_text(encoding="utf-8")


def markdown_path(job_id: str) -> Path:
    return get_settings().resolved_docparse_path / job_id / "result.md"


def purge_job_files(job_id: str) -> None:
    path = get_settings().resolved_docparse_path / job_id
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)

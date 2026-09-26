from __future__ import annotations

import hashlib
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.capabilities.site.errors import SiteError
from app.config import get_settings

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_READ_CHUNK = 1024 * 256


@dataclass
class ArchiveResult:
    entry_file: str
    file_count: int
    total_bytes: int
    content_hash: str


def _safe_name(name: str) -> str:
    if not name or name.startswith("/") or "\\" in name:
        raise SiteError("归档包含非法路径", error_type="unsafe_archive")
    if _WINDOWS_DRIVE.match(name):
        raise SiteError("归档包含非法路径", error_type="unsafe_archive")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise SiteError("归档包含路径穿越条目", error_type="unsafe_archive")
    return "/".join(parts)


def _file_infos(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos: list[zipfile.ZipInfo] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise SiteError("归档包含符号链接", error_type="unsafe_archive")
        infos.append(info)
    return infos


def _strip_root(pairs: list[tuple[zipfile.ZipInfo, str]]) -> list[tuple[zipfile.ZipInfo, str]]:
    names = [name for _, name in pairs]
    if names and all("/" in name for name in names):
        roots = {name.split("/", 1)[0] for name in names}
        if len(roots) == 1:
            prefix = next(iter(roots)) + "/"
            return [(info, name[len(prefix):]) for info, name in pairs if name != prefix]
    return pairs


def _resolve_entry(names: list[str], entry: str | None, default_entry: str) -> str:
    candidates = set(names)
    if entry:
        cleaned = entry.lstrip("/")
        if cleaned in candidates:
            return cleaned
        raise SiteError(f"归档中不存在入口文件 {entry}")
    if default_entry in candidates:
        return default_entry
    htmls = [name for name in names if "/" not in name and name.lower().endswith(".html")]
    if len(htmls) == 1:
        return htmls[0]
    raise SiteError("归档中找不到入口文件（需要 index.html 或显式 entry）")


def _hash_entries(entries: list[tuple[str, str]]) -> str:
    hasher = hashlib.sha256()
    for name, digest in sorted(entries):
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(digest.encode("ascii"))
        hasher.update(b"\0")
    return hasher.hexdigest()


def extract(archive_path: Path, dest_dir: Path, entry: str | None) -> ArchiveResult:
    settings = get_settings()
    archive_size = max(1, archive_path.stat().st_size)
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as error:
        raise SiteError("不是合法的 zip 归档") from error

    with archive:
        pairs = [(info, _safe_name(info.filename)) for info in _file_infos(archive)]
        pairs = [(info, name) for info, name in pairs if name]
        if not pairs:
            raise SiteError("归档为空")
        if len(pairs) > settings.site_max_files:
            raise SiteError(f"文件数超过上限 {settings.site_max_files}")
        declared_total = sum(info.file_size for info in archive.infolist() if not info.is_dir())
        if declared_total > settings.site_max_total_bytes:
            raise SiteError("解压后总大小超过上限")
        if declared_total > archive_size * max(1, settings.site_max_ratio):
            raise SiteError("压缩比异常，疑似 zip 炸弹", error_type="unsafe_archive")

        pairs = _strip_root(pairs)
        names = [name for _, name in pairs]
        entry_file = _resolve_entry(names, entry, settings.site_default_entry)

        dest_dir.mkdir(parents=True, exist_ok=True)
        entries: list[tuple[str, str]] = []
        total = 0
        for info, name in pairs:
            target = dest_dir / name
            if dest_dir.resolve() not in target.resolve().parents:
                raise SiteError("归档包含越界路径", error_type="unsafe_archive")
            target.parent.mkdir(parents=True, exist_ok=True)
            hasher = hashlib.sha256()
            written = 0
            try:
                with archive.open(info) as source, target.open("wb") as output:
                    while True:
                        chunk = source.read(_READ_CHUNK)
                        if not chunk:
                            break
                        written += len(chunk)
                        total += len(chunk)
                        if written > settings.site_max_file_bytes:
                            raise SiteError(f"单个文件超过上限 {settings.site_max_file_bytes} 字节")
                        if total > settings.site_max_total_bytes:
                            raise SiteError("解压后总大小超过上限")
                        hasher.update(chunk)
                        output.write(chunk)
            except SiteError:
                raise
            except (RuntimeError, OSError, zipfile.BadZipFile) as error:
                raise SiteError("归档解压失败", error_type="unsafe_archive") from error
            entries.append((name, hasher.hexdigest()))

    return ArchiveResult(
        entry_file=entry_file,
        file_count=len(entries),
        total_bytes=total,
        content_hash=_hash_entries(entries),
    )

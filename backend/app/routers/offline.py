"""离线下载接口。

同一组路由会在两个前缀下挂载：
- `/api/admin/offline`：管理后台使用，管理员 JWT 鉴权；
- `/api/public/offline`：对外公开页使用，公共口令门禁鉴权。

下载类接口会先把文件留一份到服务器缓存（`offline/cache.py`），命中缓存后直接返回，
不再回源；`/cache` 暴露已缓存列表供前端展示。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import OfflineDownload
from app.offline import cache, chrome, docker, edge, msstore, vscode
from app.offline.errors import OfflineError
from app.offline.http import fetch_bytes
from app.offline.registry import serialize_providers

router = APIRouter(tags=["offline"])
admin_router = APIRouter(tags=["offline-admin"])
# 图标是非敏感小文件，单独挂一个免鉴权路由，方便 <img> 直接引用
icon_router = APIRouter(tags=["offline-icon"])

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", (text or "").lower()).strip("-")[:80]


def _http(error: OfflineError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"error": {"type": error.error_type, "message": error.message}},
    )


# ---- 元信息 ----
@router.get("/providers")
def list_providers() -> dict:
    return {"providers": serialize_providers()}


# ---- 缓存列表 / 命中下载 ----
@router.get("/cache")
def list_cache(
    provider: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> dict:
    rows = cache.list_cached(db, provider=provider, limit=limit)
    return {"items": [cache.serialize(row) for row in rows], "total_bytes": cache.total_bytes(db)}


@router.get("/cache/{item_id}/download")
def download_cache(item_id: int, db: Session = Depends(get_db)) -> FileResponse:
    try:
        row = cache.require_cached(db, item_id)
    except OfflineError as error:
        raise _http(error) from error
    cache.touch(db, row)
    return cache.file_response(row)


@icon_router.get("/cache/{item_id}/icon")
def cache_icon(item_id: int, db: Session = Depends(get_db)) -> FileResponse:
    row = db.get(OfflineDownload, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="缓存不存在")
    try:
        return cache.icon_response(row)
    except OfflineError as error:
        raise _http(error) from error

@admin_router.delete("/cache/{item_id}")
def delete_cache(item_id: int, db: Session = Depends(get_db)) -> dict:
    if not cache.delete_cached(db, item_id):
        raise HTTPException(status_code=404, detail="缓存不存在")
    return {"ok": True}


# ---- VSCode ----
class VscodeQueryBody(BaseModel):
    query: str = ""


async def _fetch_vscode(
    db: Session, publisher: str, extension: str, version: str, *, refresh: bool = False
) -> OfflineDownload:
    key = f"vscode:{publisher}.{extension}:{version}"
    if not refresh:
        cached = cache.get_cached(db, key)
        if cached is not None:
            cache.touch(db, cached)
            return cached
    # 先回源取文件，成功后再覆盖旧缓存，避免失败把已有缓存删掉
    src_path, filename = await vscode.download_vsix(publisher, extension, version)
    if refresh:
        cache.delete_by_key(db, key)
    return cache.store_file(
        db,
        provider="vscode",
        key=key,
        src_path=src_path,
        filename=filename,
        content_type="application/octet-stream",
        title=f"{publisher}.{extension}",
        subtitle=version,
        source=vscode.vsix_url(publisher, extension, version),
    )


@router.post("/vscode/query")
async def vscode_query(payload: VscodeQueryBody) -> dict:
    try:
        return await vscode.query(payload.query)
    except OfflineError as error:
        raise _http(error) from error


@router.get("/vscode/download")
async def vscode_download(
    publisher: str = "",
    extension: str = "",
    version: str = "",
    db: Session = Depends(get_db),
) -> FileResponse:
    if not publisher or not extension or not version:
        raise HTTPException(status_code=400, detail={"error": {"type": "invalid_request", "message": "缺少 publisher/extension/version"}})
    try:
        row = await _fetch_vscode(db, publisher, extension, version)
    except OfflineError as error:
        raise _http(error) from error
    return cache.file_response(row)


# ---- Chrome ----
async def _fetch_chrome(db: Session, normalized: str, fmt: str, *, refresh: bool = False) -> OfflineDownload:
    key = f"chrome:{normalized}:{fmt}"
    if not refresh:
        cached = cache.get_cached(db, key)
        if cached is not None:
            cache.touch(db, cached)
            return cached
    meta: dict = {}
    try:
        meta = await chrome.detail(normalized)
    except OfflineError:
        meta = {}
    data, _filename, content_type = await chrome.download(normalized, fmt)
    slug = _slug(str(meta.get("name") or ""))
    filename = f"{slug or normalized}.{fmt}"
    icon_url = str(meta.get("icon") or "")
    icon_bytes = await fetch_bytes(icon_url)
    if refresh:
        cache.delete_by_key(db, key)
    return cache.store_bytes(
        db,
        provider="chrome",
        key=key,
        data=data,
        filename=filename,
        content_type=content_type,
        title=str(meta.get("name") or filename),
        subtitle=normalized,
        description=str(meta.get("description") or ""),
        icon_url=icon_url,
        icon_bytes=icon_bytes,
        source=f"https://chromewebstore.google.com/detail/{normalized}",
    )


@router.get("/chrome/search")
async def chrome_search(q: str = "") -> dict:
    try:
        return {"results": await chrome.search(q)}
    except OfflineError as error:
        raise _http(error) from error


@router.get("/chrome/detail")
async def chrome_detail(id: str = "") -> dict:
    try:
        return await chrome.detail(id)
    except OfflineError as error:
        raise _http(error) from error


@router.get("/chrome/download")
async def chrome_download(id: str = "", format: str = "crx", db: Session = Depends(get_db)) -> FileResponse:
    fmt = format if format in ("crx", "zip") else "crx"
    try:
        normalized = chrome.normalize_id(id)
    except OfflineError as error:
        raise _http(error) from error
    try:
        row = await _fetch_chrome(db, normalized, fmt)
    except OfflineError as error:
        raise _http(error) from error
    return cache.file_response(row)


# ---- Edge ----
async def _fetch_edge(db: Session, normalized: str, fmt: str, *, refresh: bool = False) -> OfflineDownload:
    key = f"edge:{normalized}:{fmt}"
    if not refresh:
        cached = cache.get_cached(db, key)
        if cached is not None:
            cache.touch(db, cached)
            return cached
    meta: dict = {}
    try:
        meta = await edge.detail(normalized)
    except OfflineError:
        meta = {}
    data, _filename, content_type = await edge.download(normalized, fmt)
    slug = _slug(str(meta.get("name") or ""))
    filename = f"{slug or normalized}.{fmt}"
    icon_url = str(meta.get("logoUrl") or meta.get("iconUrl") or "")
    icon_bytes = await fetch_bytes(icon_url)
    if refresh:
        cache.delete_by_key(db, key)
    return cache.store_bytes(
        db,
        provider="edge",
        key=key,
        data=data,
        filename=filename,
        content_type=content_type,
        title=str(meta.get("name") or filename),
        subtitle=normalized,
        description=str(meta.get("description") or meta.get("shortDescription") or ""),
        icon_url=icon_url,
        icon_bytes=icon_bytes,
        source=f"https://microsoftedge.microsoft.com/addons/detail/{normalized}",
    )


@router.get("/edge/search")
async def edge_search(q: str = "") -> dict:
    try:
        return {"results": await edge.search(q)}
    except OfflineError as error:
        raise _http(error) from error


@router.get("/edge/detail")
async def edge_detail(query: str = "") -> dict:
    try:
        return await edge.detail(query)
    except OfflineError as error:
        raise _http(error) from error


@router.get("/edge/download")
async def edge_download(id: str = "", format: str = "crx", db: Session = Depends(get_db)) -> FileResponse:
    fmt = format if format in ("crx", "zip") else "crx"
    normalized = (id or "").strip().lower()
    try:
        row = await _fetch_edge(db, normalized, fmt)
    except OfflineError as error:
        raise _http(error) from error
    return cache.file_response(row)


# ---- Docker ----
def _docker_ref(query: str, namespace: str, repository: str, tag: str) -> dict:
    if repository:
        return {
            "registry": "docker.io",
            "namespace": namespace or docker.DEFAULT_NAMESPACE,
            "repository": repository,
            "tag": tag or docker.DEFAULT_TAG,
        }
    return docker.parse_reference(query)


@router.get("/docker/tags")
async def docker_tags(query: str = "", namespace: str = "", repository: str = "") -> dict:
    try:
        ref = _docker_ref(query, namespace, repository, docker.DEFAULT_TAG)
        return await docker.tags(ref["namespace"], ref["repository"])
    except OfflineError as error:
        raise _http(error) from error


@router.get("/docker/search")
async def docker_search(q: str = "", page_size: int = 5) -> dict:
    try:
        return await docker.search(q, page_size)
    except OfflineError as error:
        raise _http(error) from error


@router.get("/docker/auth")
async def docker_auth(query: str = "", namespace: str = "", repository: str = "") -> dict:
    try:
        ref = _docker_ref(query, namespace, repository, docker.DEFAULT_TAG)
        return await docker.auth(ref["namespace"], ref["repository"])
    except OfflineError as error:
        raise _http(error) from error


@router.get("/docker/manifest")
async def docker_manifest(
    query: str = "",
    namespace: str = "",
    repository: str = "",
    tag: str = "latest",
    token: str = "",
    platform: str | None = None,
) -> dict:
    if not token:
        raise HTTPException(status_code=401, detail={"error": {"type": "invalid_request", "message": "认证令牌是必需的"}})
    try:
        ref = _docker_ref(query, namespace, repository, tag)
        return await docker.manifest(ref["namespace"], ref["repository"], ref["tag"], token, platform)
    except OfflineError as error:
        raise _http(error) from error


@router.get("/docker/layer")
async def docker_layer(
    query: str = "",
    namespace: str = "",
    repository: str = "",
    digest: str = "",
    token: str = "",
) -> StreamingResponse:
    if not digest or not token:
        raise HTTPException(status_code=400, detail={"error": {"type": "invalid_request", "message": "digest 与 token 是必需的"}})
    try:
        ref = _docker_ref(query, namespace, repository, "")
    except OfflineError as error:
        raise _http(error) from error

    async def stream() -> AsyncIterator[bytes]:
        async for chunk in docker.stream_layer(ref["namespace"], ref["repository"], digest, token):
            yield chunk

    return StreamingResponse(stream(), media_type="application/octet-stream")


async def _fetch_docker(
    db: Session, namespace: str, repository: str, tag: str, platform: str | None, *, refresh: bool = False
) -> OfflineDownload:
    key = f"docker:{namespace}/{repository}:{tag}:{platform or 'default'}"
    if not refresh:
        cached = cache.get_cached(db, key)
        if cached is not None:
            cache.touch(db, cached)
            return cached
    path, filename = await docker.build_image_tar(namespace, repository, tag, platform)
    if refresh:
        cache.delete_by_key(db, key)
    return cache.store_file(
        db,
        provider="docker",
        key=key,
        src_path=path,
        filename=filename,
        content_type="application/x-tar",
        title=f"{namespace}/{repository}:{tag}",
        subtitle=platform or "linux/amd64",
        source=f"https://hub.docker.com/r/{namespace}/{repository}",
    )


@router.get("/docker/package")
async def docker_package(query: str = "", platform: str | None = None, db: Session = Depends(get_db)) -> FileResponse:
    try:
        ref = docker.parse_reference(query)
        row = await _fetch_docker(db, ref["namespace"], ref["repository"], ref["tag"], platform)
    except OfflineError as error:
        raise _http(error) from error
    return cache.file_response(row)


# ---- Microsoft Store ----
async def _fetch_msstore(db: Session, url: str, filename: str, *, refresh: bool = False) -> OfflineDownload:
    key = f"msstore:{hashlib.sha1((url or '').encode('utf-8')).hexdigest()}:{filename or ''}"
    if not refresh:
        cached = cache.get_cached(db, key)
        if cached is not None:
            cache.touch(db, cached)
            return cached
    src_path, name, content_type = await msstore.fetch_to_file(url, filename)
    if refresh:
        cache.delete_by_key(db, key)
    return cache.store_file(
        db,
        provider="msstore",
        key=key,
        src_path=src_path,
        filename=name,
        content_type=content_type,
        title=name,
        subtitle="Microsoft Store",
        source=url,
    )


@router.get("/msstore/resolve")
async def msstore_resolve(
    type: str = "url",
    query: str = "",
    market: str = "US",
    language: str = "en-us",
    ring: str = "RP",
) -> dict:
    try:
        return await msstore.resolve(type, query, market, language, ring)
    except OfflineError as error:
        raise _http(error) from error


@router.get("/msstore/download")
async def msstore_download(url: str = "", filename: str = "", db: Session = Depends(get_db)) -> FileResponse:
    try:
        row = await _fetch_msstore(db, url, filename)
    except OfflineError as error:
        raise _http(error) from error
    return cache.file_response(row)


# ---- 缓存更新（回源重取，覆盖旧缓存）----
def _parse_vscode_key(key: str) -> tuple[str, str, str]:
    ref, _, version = key.partition(":")[2].rpartition(":")
    publisher, _, extension = ref.partition(".")
    return publisher, extension, version


def _parse_id_fmt_key(key: str, prefix: str, default_fmt: str = "crx") -> tuple[str, str]:
    normalized, _, fmt = key[len(prefix) :].rpartition(":")
    return normalized, (fmt if fmt in ("crx", "zip") else default_fmt)


async def _refetch(db: Session, row: OfflineDownload) -> OfflineDownload:
    provider = row.provider
    key = row.cache_key
    if provider == "vscode":
        publisher, extension, version = _parse_vscode_key(key)
        if not (publisher and extension and version):
            raise OfflineError("缓存信息不完整，无法更新", status_code=409)
        return await _fetch_vscode(db, publisher, extension, version, refresh=True)
    if provider == "chrome":
        normalized, fmt = _parse_id_fmt_key(key, "chrome:")
        if not normalized:
            raise OfflineError("缓存信息不完整，无法更新", status_code=409)
        return await _fetch_chrome(db, normalized, fmt, refresh=True)
    if provider == "edge":
        normalized, fmt = _parse_id_fmt_key(key, "edge:")
        if not normalized:
            raise OfflineError("缓存信息不完整，无法更新", status_code=409)
        return await _fetch_edge(db, normalized, fmt, refresh=True)
    if provider == "docker":
        body = key[len("docker:") :]
        ref_tag, _, platform = body.rpartition(":")
        ref, _, tag = ref_tag.rpartition(":")
        namespace, _, repository = ref.partition("/")
        if not (namespace and repository and tag):
            raise OfflineError("缓存信息不完整，无法更新", status_code=409)
        return await _fetch_docker(db, namespace, repository, tag, None if platform == "default" else platform, refresh=True)
    if provider == "msstore":
        if not row.source:
            raise OfflineError("缓存信息不完整，无法更新", status_code=409)
        return await _fetch_msstore(db, row.source, row.filename, refresh=True)
    raise OfflineError("该来源暂不支持更新缓存", status_code=400)


@admin_router.post("/cache/{item_id}/refresh")
async def refresh_cache(item_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(OfflineDownload, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="缓存不存在")
    try:
        updated = await _refetch(db, row)
    except OfflineError as error:
        raise _http(error) from error
    return {"item": cache.serialize(updated)}

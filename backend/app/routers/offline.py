"""离线下载接口。

同一组路由会在两个前缀下挂载：
- `/api/admin/offline`：管理后台使用，管理员 JWT 鉴权；
- `/api/public/offline`：对外公开页使用，公共口令门禁鉴权。

解析（search/detail/resolve）只返回元信息，不再直接触发同步下载。
用户点击「缓存到服务器」后走 `*/cache` 入队，后台异步缓存到本地
（`app/offline/jobs.py`），进度写回 `offline_downloads`，`/cache` 暴露列表；
只有 `status=ready` 的记录才提供 `/cache/{id}/download`。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import OfflineDownload
from app.offline import cache, chrome, docker, edge, msstore, vscode
from app.offline.errors import OfflineError
from app.offline.registry import serialize_providers

router = APIRouter(tags=["offline"])
admin_router = APIRouter(tags=["offline-admin"])
# 图标是非敏感小文件，单独挂一个免鉴权路由，方便 <img> 直接引用
icon_router = APIRouter(tags=["offline-icon"])


def _http(error: OfflineError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"error": {"type": error.error_type, "message": error.message}},
    )


def _cache_key_from_ref(provider: str, normalized: str, fmt: str) -> str:
    return f"{provider}:{normalized}:{fmt}"


def _reuse(db: Session, key: str) -> dict | None:
    """已就绪或进行中的记录直接复用，返回序列化结果；失败/不存在返回 None。"""
    row = cache.get_by_key(db, key)
    if row is None or cache.status_of(row) == "failed":
        return None
    if cache.status_of(row) == "ready":
        cache.touch(db, row)
        db.commit()
    return cache.serialize(row)


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
    db.commit()
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


class VscodeCacheBody(BaseModel):
    publisher: str = ""
    extension: str = ""
    version: str = ""
    display_name: str = ""
    filename: str = ""


@router.post("/vscode/query")
async def vscode_query(payload: VscodeQueryBody) -> dict:
    try:
        return await vscode.query(payload.query)
    except OfflineError as error:
        raise _http(error) from error


@router.post("/vscode/cache")
def vscode_cache(payload: VscodeCacheBody, db: Session = Depends(get_db)) -> dict:
    publisher = payload.publisher.strip()
    extension = payload.extension.strip()
    version = payload.version.strip()
    if not (publisher and extension and version):
        raise HTTPException(status_code=400, detail={"error": {"type": "invalid_request", "message": "缺少 publisher/extension/version"}})
    key = f"vscode:{publisher}.{extension}:{version}"
    existing = _reuse(db, key)
    if existing is not None:
        return {"item": existing}
    row = cache.enqueue(
        db,
        provider="vscode",
        key=key,
        title=payload.display_name or f"{publisher}.{extension}",
        subtitle=version,
        filename=payload.filename or f"{extension}-{version}.vsix",
        content_type="application/octet-stream",
        source=vscode.vsix_url(publisher, extension, version),
        request={"publisher": publisher, "extension": extension, "version": version},
    )
    db.commit()
    return {"item": cache.serialize(row)}


# ---- Chrome / Edge ----
class ExtensionCacheBody(BaseModel):
    id: str = ""
    format: str = "crx"
    name: str = ""
    description: str = ""
    icon_url: str = ""


def _enqueue_extension(db: Session, payload: ExtensionCacheBody, provider: str) -> dict:
    fmt = payload.format if payload.format in ("crx", "zip") else "crx"
    if provider == "chrome":
        try:
            normalized = chrome.normalize_id(payload.id)
        except OfflineError as error:
            raise _http(error) from error
        source = f"https://chromewebstore.google.com/detail/{normalized}"
    else:
        normalized = (payload.id or "").strip().lower()
        if not (len(normalized) == 32 and normalized.isalnum()):
            raise HTTPException(status_code=400, detail={"error": {"type": "invalid_request", "message": "无效的 Edge 扩展 ID"}})
        source = f"https://microsoftedge.microsoft.com/addons/detail/{normalized}"
    key = _cache_key_from_ref(provider, normalized, fmt)
    existing = _reuse(db, key)
    if existing is not None:
        return {"item": existing}
    content_type = "application/zip" if fmt == "zip" else "application/x-chrome-extension"
    row = cache.enqueue(
        db,
        provider=provider,
        key=key,
        title=payload.name or normalized,
        subtitle=normalized,
        description=payload.description,
        icon_url=payload.icon_url,
        filename=f"{normalized}.{fmt}",
        content_type=content_type,
        source=source,
        request={"id": normalized, "format": fmt},
    )
    db.commit()
    return {"item": cache.serialize(row)}


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


@router.post("/chrome/cache")
def chrome_cache(payload: ExtensionCacheBody, db: Session = Depends(get_db)) -> dict:
    return _enqueue_extension(db, payload, "chrome")


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


@router.post("/edge/cache")
def edge_cache(payload: ExtensionCacheBody, db: Session = Depends(get_db)) -> dict:
    return _enqueue_extension(db, payload, "edge")


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


class DockerCacheBody(BaseModel):
    query: str = ""
    platform: str | None = None
    filename: str = ""


@router.post("/docker/cache")
def docker_cache(payload: DockerCacheBody, db: Session = Depends(get_db)) -> dict:
    try:
        ref = docker.parse_reference(payload.query)
    except OfflineError as error:
        raise _http(error) from error
    platform = payload.platform or None
    namespace, repository, tag = ref["namespace"], ref["repository"], ref["tag"]
    key = f"docker:{namespace}/{repository}:{tag}:{platform or 'default'}"
    existing = _reuse(db, key)
    if existing is not None:
        return {"item": existing}
    row = cache.enqueue(
        db,
        provider="docker",
        key=key,
        title=f"{namespace}/{repository}:{tag}",
        subtitle=platform or "linux/amd64",
        filename=payload.filename or f"{namespace}-{repository}-{tag}.tar",
        content_type="application/x-tar",
        source=f"https://hub.docker.com/r/{namespace}/{repository}",
        request={"query": payload.query.strip(), "platform": platform},
    )
    db.commit()
    return {"item": cache.serialize(row)}


# ---- Microsoft Store ----
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


class MsStoreCacheBody(BaseModel):
    url: str = ""
    filename: str = ""
    title: str = ""


@router.post("/msstore/cache")
def msstore_cache(payload: MsStoreCacheBody, db: Session = Depends(get_db)) -> dict:
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail={"error": {"type": "invalid_request", "message": "缺少下载地址"}})
    filename = payload.filename.strip()
    key = f"msstore:{hashlib.sha1(url.encode('utf-8')).hexdigest()}:{filename}"
    existing = _reuse(db, key)
    if existing is not None:
        return {"item": existing}
    row = cache.enqueue(
        db,
        provider="msstore",
        key=key,
        title=payload.title or filename or "Microsoft Store 安装包",
        subtitle="Microsoft Store",
        filename=filename or "download.bin",
        content_type="application/octet-stream",
        source=url,
        request={"url": url, "filename": filename},
    )
    db.commit()
    return {"item": cache.serialize(row)}


# ---- 缓存更新（回源重取，覆盖旧缓存）----
def _parse_vscode_key(key: str) -> tuple[str, str, str]:
    ref, _, version = key.partition(":")[2].rpartition(":")
    publisher, _, extension = ref.partition(".")
    return publisher, extension, version


def _parse_id_fmt_key(key: str, prefix: str, default_fmt: str = "crx") -> tuple[str, str]:
    normalized, _, fmt = key[len(prefix):].rpartition(":")
    return normalized, (fmt if fmt in ("crx", "zip") else default_fmt)


def _parse_docker_key(key: str) -> tuple[str, str, str, str]:
    body = key[len("docker:"):]
    ref_tag, _, platform = body.rpartition(":")
    ref, _, tag = ref_tag.rpartition(":")
    namespace, _, repository = ref.partition("/")
    return namespace, repository, tag, platform


def _request_from_row(row: OfflineDownload) -> dict | None:
    try:
        request = json.loads(row.request_json or "{}")
    except ValueError:
        request = {}
    if isinstance(request, dict) and request:
        return request
    provider = row.provider
    key = row.cache_key
    if provider == "vscode":
        publisher, extension, version = _parse_vscode_key(key)
        if publisher and extension and version:
            return {"publisher": publisher, "extension": extension, "version": version}
    elif provider in ("chrome", "edge"):
        normalized, fmt = _parse_id_fmt_key(key, f"{provider}:")
        if normalized:
            return {"id": normalized, "format": fmt}
    elif provider == "docker":
        namespace, repository, tag, platform = _parse_docker_key(key)
        if namespace and repository and tag:
            return {"query": f"{namespace}/{repository}:{tag}", "platform": None if platform == "default" else platform}
    elif provider == "msstore" and row.source:
        return {"url": row.source, "filename": row.filename}
    return None


@admin_router.post("/cache/{item_id}/refresh")
def refresh_cache(item_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(OfflineDownload, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="缓存不存在")
    request = _request_from_row(row)
    if request is None:
        raise HTTPException(status_code=409, detail="缓存信息不完整，无法更新")
    cache.requeue(db, row, request=request)
    db.commit()
    return {"item": cache.serialize(row)}

"""Docker 镜像：引用解析、标签/搜索、registry 鉴权、manifest、层下载与 docker load 打包。"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import tarfile
import tempfile

import httpx

from app.offline.errors import OfflineError, UpstreamError
from app.offline.http import (
    DOCKER_TIMEOUT,
    LAYER_TIMEOUT,
    get_json,
    make_client,
)

REGISTRY = "registry-1.docker.io"
AUTH_URL = "https://auth.docker.io/token"
DEFAULT_TAG = "latest"
DEFAULT_NAMESPACE = "library"

_MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.index.v1+json",
    ]
)
_LIST_TYPES = {
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
}
_GZIP_MAGIC = b"\x1f\x8b"
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def parse_reference(query: str) -> dict:
    """解析 `nginx:latest` / `library/nginx` / `docker.io/library/nginx:latest` 等。"""
    raw = (query or "").strip()
    if not raw:
        raise OfflineError("请输入镜像名称，例如 nginx:latest")
    if "://" in raw:
        # 支持 hub.docker.com/r/library/nginx 形式
        path = raw.split("://", 1)[1]
        parts = path.split("/")
        if parts and parts[0] in {"hub.docker.com", "registry.hub.docker.com"}:
            raw = "/".join(parts[2:]) if len(parts) >= 3 and parts[1] == "r" else "/".join(parts[1:])
        else:
            raw = "/".join(parts[1:])

    raw = raw.split("@", 1)[0]  # 去掉 digest 引用
    tag = DEFAULT_TAG
    if ":" in raw.rsplit("/", 1)[-1]:
        raw, tag = raw.rsplit(":", 1)

    segments = [segment for segment in raw.split("/") if segment]
    if not segments:
        raise OfflineError("请输入镜像名称，例如 nginx:latest")

    registry = "docker.io"
    if len(segments) >= 2 and ("." in segments[0] or ":" in segments[0]):
        registry = segments[0]
        segments = segments[1:]

    namespace = segments[0] if len(segments) >= 2 else DEFAULT_NAMESPACE
    repository = segments[-1]
    if not re.match(r"^[a-z0-9][a-z0-9._/-]*$", repository):
        raise OfflineError("镜像名称不合法")
    return {"registry": registry, "namespace": namespace, "repository": repository, "tag": tag or DEFAULT_TAG}


async def tags(namespace: str, repository: str) -> dict:
    namespace = namespace or DEFAULT_NAMESPACE
    url = f"https://registry.hub.docker.com/v2/repositories/{namespace}/{repository}/tags"
    return await get_json(url, params={"page_size": 100})


async def search(query: str, page_size: int = 5) -> dict:
    keyword = (query or "").strip()
    if not keyword:
        raise OfflineError("搜索关键词是必需的")
    size = max(1, min(100, int(page_size or 5)))
    url = "https://hub.docker.com/v2/search/repositories/"
    return await get_json(url, params={"query": keyword, "page_size": size})


async def auth(namespace: str, repository: str) -> dict:
    repo_path = f"{namespace or DEFAULT_NAMESPACE}/{repository}"
    url = f"{AUTH_URL}?service=registry.docker.io&scope=repository:{repo_path}:pull"
    return await get_json(url)


async def _fetch_manifest(namespace: str, repository: str, reference: str, token: str, *, accept: str = _MANIFEST_TYPES) -> dict:
    repo_path = f"{namespace or DEFAULT_NAMESPACE}/{repository}"
    url = f"https://{REGISTRY}/v2/{repo_path}/manifests/{reference}"
    async with make_client(timeout=DOCKER_TIMEOUT) as client:
        response = await client.get(url, headers={"Authorization": f"Bearer {token}", "Accept": accept})
    if response.status_code >= 400:
        raise UpstreamError(f"Docker Registry 响应错误: {response.status_code}", status_code=response.status_code)
    try:
        return response.json()
    except ValueError as error:
        raise UpstreamError("Docker Registry 返回了非 JSON 响应") from error


def _select_platform(manifests: list[dict], platform: str | None) -> dict:
    target = (platform or "linux/amd64").split("/")
    target_os = target[0]
    target_arch = target[1] if len(target) > 1 else "amd64"
    target_variant = target[2] if len(target) > 2 else None

    def matches(entry: dict, strict_variant: bool) -> bool:
        plat = entry.get("platform") or {}
        if plat.get("os") != target_os or plat.get("architecture") != target_arch:
            return False
        if strict_variant and target_variant:
            return plat.get("variant") == target_variant
        if strict_variant and target_arch == "arm":
            return not plat.get("variant")
        return True

    for entry in manifests:
        if matches(entry, strict_variant=True):
            return entry
    for entry in manifests:
        if matches(entry, strict_variant=False):
            return entry
    raise OfflineError(f"未找到 {platform or 'linux/amd64'} 平台的镜像", status_code=404)


async def manifest(namespace: str, repository: str, tag: str, token: str, platform: str | None = None) -> dict:
    data = await _fetch_manifest(namespace, repository, tag or DEFAULT_TAG, token)
    if data.get("mediaType") in _LIST_TYPES:
        manifests = data.get("manifests") or []
        if not platform:
            platforms = [
                {
                    "os": (m.get("platform") or {}).get("os"),
                    "architecture": (m.get("platform") or {}).get("architecture"),
                    "variant": (m.get("platform") or {}).get("variant"),
                    "digest": m.get("digest"),
                }
                for m in manifests
                if (m.get("platform") or {}).get("architecture") not in (None, "unknown")
                and (m.get("platform") or {}).get("os") not in (None, "unknown")
            ]
            return {"type": "manifest_list", "platforms": platforms}
        selected = _select_platform(manifests, platform)
        return await _fetch_manifest(
            namespace,
            repository,
            selected["digest"],
            token,
            accept="application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json",
        )
    return data


def _blob_url(namespace: str, repository: str, digest: str) -> str:
    repo_path = f"{namespace or DEFAULT_NAMESPACE}/{repository}"
    return f"https://{REGISTRY}/v2/{repo_path}/blobs/{digest}"


async def stream_layer(namespace: str, repository: str, digest: str, token: str):
    """按块流式返回某一层；配合 StreamingResponse 使用。"""
    url = _blob_url(namespace, repository, digest)
    async with httpx.AsyncClient(timeout=LAYER_TIMEOUT, follow_redirects=True) as client, client.stream(
        "GET", url, headers={"Authorization": f"Bearer {token}"}
    ) as response:
        if response.status_code >= 400:
            raise UpstreamError(f"层下载失败: {response.status_code}", status_code=response.status_code)
        async for chunk in response.aiter_bytes():
            yield chunk


def _decompress_layer(data: bytes) -> bytes:
    if data[:2] == _GZIP_MAGIC:
        return gzip.decompress(data)
    if data[:4] == _ZSTD_MAGIC:
        raise OfflineError("暂不支持 zstd 压缩层")
    return data


async def _download_blob(namespace: str, repository: str, digest: str, token: str, on_progress=None) -> bytes:
    url = _blob_url(namespace, repository, digest)
    async with make_client(timeout=LAYER_TIMEOUT) as client, client.stream(
        "GET", url, headers={"Authorization": f"Bearer {token}"}
    ) as response:
        if response.status_code >= 400:
            raise UpstreamError(f"下载层失败: {response.status_code}", status_code=response.status_code)
        buffer = bytearray()
        downloaded = 0
        async for chunk in response.aiter_bytes():
            buffer.extend(chunk)
            downloaded += len(chunk)
            if on_progress:
                on_progress(downloaded)
        return bytes(buffer)


async def build_image_tar(namespace: str, repository: str, tag: str, platform: str | None, on_progress=None) -> tuple[str, str]:
    """构建 docker load 兼容的 tar，返回 (临时文件路径, 下载文件名)。"""
    namespace = namespace or DEFAULT_NAMESPACE
    tag = tag or DEFAULT_TAG
    token_data = await auth(namespace, repository)
    token = token_data.get("token") or token_data.get("access_token")
    if not token:
        raise UpstreamError("未获取到 Docker 认证令牌")

    data = await _fetch_manifest(namespace, repository, tag, token)
    resolved_platform = platform
    if data.get("mediaType") in _LIST_TYPES:
        resolved_platform = platform or "linux/amd64"
        selected = _select_platform(data.get("manifests") or [], resolved_platform)
        data = await _fetch_manifest(
            namespace,
            repository,
            selected["digest"],
            token,
            accept="application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json",
        )

    config_descriptor = data.get("config") or {}
    layers = [layer for layer in (data.get("layers") or []) if layer.get("digest")]
    config_digest = config_descriptor.get("digest")
    if not config_digest or not layers:
        raise UpstreamError("镜像 manifest 缺少 config 或 layers")

    total_bytes = int(config_descriptor.get("size") or 0) + sum(int(layer.get("size") or 0) for layer in layers)
    state = {"done": 0}

    def report(downloaded: int) -> None:
        if on_progress:
            on_progress(state["done"] + int(downloaded), total_bytes)

    config_blob = await _download_blob(namespace, repository, config_digest, token, on_progress=report)
    state["done"] += len(config_blob)

    config_name = f"{config_digest.replace(':', '_')}.json"
    repo_tag = f"{namespace}/{repository}:{tag}"
    manifest_layers: list[str] = []
    layer_dirs: list[tuple[str, str]] = []  # (dir_name, digest)

    for layer in layers:
        digest = layer["digest"]
        dir_name = digest.replace(":", "_")
        manifest_layers.append(f"{dir_name}/layer.tar")
        layer_dirs.append((dir_name, digest))

    with tempfile.NamedTemporaryFile(prefix="offline-image-", suffix=".tar", delete=False) as tmp:
        path = tmp.name

    try:
        with tarfile.open(path, "w") as tar:
            def add_bytes(name: str, payload: bytes) -> None:
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mtime = int(os.path.getmtime(path))
                tar.addfile(info, io.BytesIO(payload))

            add_bytes(
                "manifest.json",
                json.dumps(
                    [{"Config": config_name, "RepoTags": [repo_tag], "Layers": manifest_layers}]
                ).encode("utf-8"),
            )
            add_bytes(config_name, config_blob)

            for dir_name, digest in layer_dirs:
                raw = await _download_blob(namespace, repository, digest, token, on_progress=report)
                state["done"] += len(raw)
                layer_tar = _decompress_layer(raw)
                add_bytes(f"{dir_name}/layer.tar", layer_tar)
                add_bytes(f"{dir_name}/VERSION", b"1.0")
                add_bytes(
                    f"{dir_name}/json",
                    json.dumps({"id": dir_name, "parent": None, "created": "1970-01-01T00:00:00Z"}).encode("utf-8"),
                )
    except Exception:
        if os.path.exists(path):
            os.unlink(path)
        raise

    suffix = ""
    if resolved_platform and "/" in resolved_platform:
        arch = resolved_platform.split("/")[1]
        if arch and arch != "amd64":
            suffix = f"-{arch}"
    filename = f"{namespace}-{repository}-{tag}{suffix}.tar"
    return path, filename

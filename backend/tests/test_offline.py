"""离线下载：解析工具单测 + 接口鉴权/门禁复用。"""

from __future__ import annotations

import io
import struct
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.db import get_session_factory
from app.offline import cache as offline_cache
from app.offline import docker, edge, msstore, vscode
from app.offline.crx import crx_to_zip
from app.offline.errors import OfflineError
from app.services import info_public_gate as gate


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", "hello")
    return buffer.getvalue()


def test_docker_parse_reference_variants() -> None:
    assert docker.parse_reference("nginx:latest") == {
        "registry": "docker.io",
        "namespace": "library",
        "repository": "nginx",
        "tag": "latest",
    }
    assert docker.parse_reference("library/nginx")["tag"] == "latest"
    assert docker.parse_reference("myorg/app:v1")["namespace"] == "myorg"
    hub = docker.parse_reference("https://hub.docker.com/r/library/nginx")
    assert hub["namespace"] == "library" and hub["repository"] == "nginx"


def test_docker_parse_reference_rejects_empty() -> None:
    with pytest.raises(OfflineError):
        docker.parse_reference("   ")


def test_edge_parse_query_variants() -> None:
    assert edge.parse_query("a" * 32) == {"type": "crxId", "value": "a" * 32}
    assert edge.parse_query("AbCd1234EfGh") == {"type": "storeProductId", "value": "ABCD1234EFGH"}
    url = "https://microsoftedge.microsoft.com/addons/detail/foo/abcdefghijklmnopqrstuvwxyzabcdef"
    parsed = edge.parse_query(url)
    assert parsed is not None and parsed["type"] == "crxId"
    assert edge.parse_query("not a valid input!!") is None


def test_vscode_parse_item_name() -> None:
    publisher, extension = vscode.parse_item_name(
        "https://marketplace.visualstudio.com/items?itemName=ms-python.python"
    )
    assert (publisher, extension) == ("ms-python", "python")
    assert vscode.parse_item_name("a.b") == ("a", "b")
    with pytest.raises(OfflineError):
        vscode.parse_item_name("no-dot-here")


def test_crx_to_zip_strips_header() -> None:
    inner = _zip_bytes()
    crx = b"Cr24" + struct.pack("<I", 3) + struct.pack("<I", 4) + b"head" + inner
    assert crx_to_zip(crx) == inner
    # 已经是 ZIP 时原样返回
    assert crx_to_zip(inner) == inner


def test_msstore_identifier_parsing() -> None:
    assert msstore._extract_store_identifier_from_url("9N0DX20HK701") == "9N0DX20HK701"
    assert (
        msstore._extract_store_identifier_from_url("https://apps.microsoft.com/detail/9n0dx20hk701")
        == "9N0DX20HK701"
    )
    with pytest.raises(OfflineError):
        msstore._normalize_store_identifier("ProductId", "short")


def test_offline_providers_auth(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get("/api/admin/offline/providers").status_code == 401
    response = client.get("/api/admin/offline/providers", headers=auth_headers)
    assert response.status_code == 200, response.text
    slugs = [provider["slug"] for provider in response.json()["providers"]]
    assert slugs == ["vscode", "chrome", "edge", "docker", "msstore"]


def test_offline_public_gate_independent(client: TestClient, auth_headers: dict[str, str]) -> None:
    gate.unlock_gate.reset()
    # 默认匿名可访问
    assert client.get("/api/public/offline/providers").status_code == 200

    # 仅开启资讯门禁：离线公开页不受影响
    response = client.put(
        "/api/admin/public-gate?scope=info",
        json={"password": "infosecret1"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    client.cookies.clear()
    assert client.get("/api/public/offline/providers").status_code == 200

    # 开启离线门禁（与资讯相互独立）
    response = client.put(
        "/api/admin/public-gate?scope=offline",
        json={"password": "offsecret1"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    client.cookies.clear()
    assert client.get("/api/public/offline/providers").status_code == 401

    # 资讯口令无法解锁离线；离线口令可以
    gate.unlock_gate.reset()
    wrong = client.post("/api/public/access/offline/unlock", json={"password": "infosecret1"})
    assert wrong.status_code == 401
    unlocked = client.post("/api/public/access/offline/unlock", json={"password": "offsecret1"})
    assert unlocked.status_code == 200, unlocked.text
    assert client.get("/api/public/offline/providers").status_code == 200

    client.post("/api/public/access/offline/lock")
    assert client.get("/api/public/offline/providers").status_code == 401


def _store_cache(**kwargs) -> int:
    session = get_session_factory()()
    try:
        row = offline_cache.store_bytes(
            session,
            provider=kwargs.get("provider", "chrome"),
            key=kwargs["key"],
            data=kwargs.get("data", b"hello-world"),
            filename=kwargs.get("filename", "test.crx"),
            content_type=kwargs.get("content_type", "application/x-chrome-extension"),
            title=kwargs.get("title", "Test"),
            subtitle=kwargs.get("subtitle", "CRX"),
            source=kwargs.get("source", "https://example.com"),
        )
        session.commit()
        return int(row.id)
    finally:
        session.close()


def test_offline_cache_list_download_delete(client: TestClient, auth_headers: dict[str, str]) -> None:
    item_id = _store_cache(key="chrome:testid:crx")

    # 公开端也能看到缓存列表（门禁未开启时匿名可访问）
    public = client.get("/api/public/offline/cache")
    assert public.status_code == 200
    assert any(item["id"] == item_id for item in public.json()["items"])

    download = client.get(f"/api/public/offline/cache/{item_id}/download")
    assert download.status_code == 200
    assert download.content == b"hello-world"

    # 删除仅管理端可用
    assert client.delete(f"/api/admin/offline/cache/{item_id}").status_code == 401
    assert client.delete(f"/api/admin/offline/cache/{item_id}", headers=auth_headers).status_code == 200
    assert client.get(f"/api/public/offline/cache/{item_id}/download").status_code == 404


def test_offline_cache_refresh_refetches(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import asyncio

    from app.offline import jobs as offline_jobs

    item_id = _store_cache(key="vscode:pub.ext:1.0.0", provider="vscode", data=b"old-bytes", filename="ext-1.0.0.vsix")

    async def fake_download(publisher: str, extension: str, version: str, on_progress=None) -> tuple[str, str]:
        path = tmp_path / "new.vsix"
        path.write_bytes(b"new-bytes")
        return str(path), f"{publisher}.{extension}-{version}.vsix"

    monkeypatch.setattr(vscode, "download_vsix", fake_download)
    monkeypatch.setattr(vscode, "vsix_url", lambda p, e, v: f"https://example.com/{p}.{e}/{v}")

    # 更新仅管理端可用
    assert client.post(f"/api/admin/offline/cache/{item_id}/refresh").status_code == 401
    assert client.post(f"/api/public/offline/cache/{item_id}/refresh").status_code == 404

    refreshed = client.post(f"/api/admin/offline/cache/{item_id}/refresh", headers=auth_headers)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["item"]["status"] == "queued"

    # refresh 只入队，手动驱动后台 worker 完成一次
    asyncio.run(offline_jobs.process_job_now(item_id))

    listed = client.get("/api/admin/offline/cache", headers=auth_headers).json()["items"]
    item = next(row for row in listed if row["id"] == item_id)
    assert item["status"] == "ready"
    assert item["size_bytes"] == len(b"new-bytes")

    download = client.get(f"/api/public/offline/cache/{item_id}/download")
    assert download.status_code == 200 and download.content == b"new-bytes"


def test_offline_cache_refresh_docker_key_parsing(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import asyncio

    from app.offline import jobs as offline_jobs

    item_id = _store_cache(key="docker:library/nginx:latest:default", provider="docker", data=b"old", filename="nginx-latest.tar")

    calls: dict[str, object] = {}

    async def fake_build(namespace: str, repository: str, tag: str, platform: str | None, on_progress=None) -> tuple[str, str]:
        calls.update(namespace=namespace, repository=repository, tag=tag, platform=platform)
        path = tmp_path / "image.tar"
        path.write_bytes(b"new-image")
        return str(path), f"{namespace}-{repository}-{tag}.tar"

    monkeypatch.setattr(docker, "build_image_tar", fake_build)
    resp = client.post(f"/api/admin/offline/cache/{item_id}/refresh", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["item"]["status"] == "queued"

    asyncio.run(offline_jobs.process_job_now(item_id))

    assert calls == {"namespace": "library", "repository": "nginx", "tag": "latest", "platform": None}
    listed = client.get("/api/admin/offline/cache", headers=auth_headers).json()["items"]
    item = next(row for row in listed if row["id"] == item_id)
    assert item["status"] == "ready"
    assert item["size_bytes"] == len(b"new-image")


def test_offline_enqueue_then_download(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import asyncio

    from app.offline import jobs as offline_jobs

    async def fake_download(publisher: str, extension: str, version: str, on_progress=None) -> tuple[str, str]:
        if on_progress:
            on_progress(5, 10)
            on_progress(10, 10)
        path = tmp_path / "ext.vsix"
        path.write_bytes(b"vsix-bytes")
        return str(path), f"{publisher}.{extension}-{version}.vsix"

    monkeypatch.setattr(vscode, "download_vsix", fake_download)
    monkeypatch.setattr(vscode, "vsix_url", lambda p, e, v: f"https://example.com/{p}.{e}/{v}")

    created = client.post(
        "/api/admin/offline/vscode/cache",
        headers=auth_headers,
        json={"publisher": "pub", "extension": "ext", "version": "1.0.0", "display_name": "示例插件"},
    )
    assert created.status_code == 200, created.text
    item = created.json()["item"]
    assert item["status"] == "queued"
    assert item["downloadable"] is False

    # 未完成前不提供下载
    assert client.get(f"/api/admin/offline/cache/{item['id']}/download", headers=auth_headers).status_code == 404

    asyncio.run(offline_jobs.process_job_now(item["id"]))

    listed = client.get("/api/admin/offline/cache", headers=auth_headers).json()["items"]
    row = next(entry for entry in listed if entry["id"] == item["id"])
    assert row["status"] == "ready"
    assert row["downloadable"] is True
    assert row["size_bytes"] == len(b"vsix-bytes")

    download = client.get(f"/api/admin/offline/cache/{item['id']}/download", headers=auth_headers)
    assert download.status_code == 200 and download.content == b"vsix-bytes"


def test_offline_worker_claim_and_reconcile(client: TestClient) -> None:
    from app.clock import utcnow
    from app.models import OfflineDownload
    from app.offline import jobs as offline_jobs

    session = get_session_factory()()
    try:
        row = OfflineDownload(
            provider="vscode",
            cache_key="vscode:worker.claim:1.0.0",
            title="worker",
            status="queued",
            stage="queued",
            percent=0,
            message="排队中",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(row)
        session.commit()
        row_id = int(row.id)
    finally:
        session.close()

    assert offline_jobs._claim_next() == row_id

    session = get_session_factory()()
    try:
        current = session.get(OfflineDownload, row_id)
        assert current is not None
        assert current.status == "caching"
        assert current.attempts == 1
        # 进程重启：中断的 caching 任务会被重新排队
        assert offline_jobs.reconcile_stuck_jobs(session) == 1
        assert session.get(OfflineDownload, row_id).status == "queued"
    finally:
        session.close()


def test_offline_cache_hit_avoids_refetch(client: TestClient, auth_headers: dict[str, str]) -> None:
    item_id = _store_cache(key="chrome:reuse:crx", data=b"cached-bytes")
    first = client.get(f"/api/admin/offline/cache/{item_id}/download", headers=auth_headers)
    second = client.get(f"/api/admin/offline/cache/{item_id}/download", headers=auth_headers)
    assert first.content == b"cached-bytes" and second.content == b"cached-bytes"

    session = get_session_factory()()
    try:
        from app.models import OfflineDownload

        row = session.get(OfflineDownload, item_id)
        assert row is not None and row.hit_count >= 2
    finally:
        session.close()

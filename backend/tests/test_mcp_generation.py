from __future__ import annotations

import time

from fastapi.testclient import TestClient
from PIL import Image

from app.generation import service, storage
from app.generation.base import GenerationUpdate


def _png_bytes() -> bytes:
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (1, 2, 3)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG_BYTES = _png_bytes()


class FakeAdapter:
    provider = "gsai"

    def submit(self, **kwargs) -> GenerationUpdate:  # noqa: ANN003
        return GenerationUpdate(status="running", progress=5, upstream_id="up-1")

    def poll(self, upstream_id: str) -> GenerationUpdate:
        return GenerationUpdate(status="succeeded", progress=100, results=["https://example.com/a.png"])


def _create_key(client: TestClient, auth_headers: dict[str, str]) -> str:
    response = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "gen-key", "capability_ids": ["generation"]},
    )
    assert response.status_code == 201, response.text
    return response.json()["key"]


def test_generation_capability_exposed(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/admin/mcp/catalog", headers=auth_headers)
    assert response.status_code == 200, response.text
    ids = [item["capability_id"] for item in response.json()["items"]]
    assert "generation" in ids


def test_key_capability_config_roundtrip(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _create_key(client, auth_headers)
    keys = client.get("/api/admin/mcp/keys", headers=auth_headers).json()
    key_id = next(item["id"] for item in keys if item["name"] == "gen-key")

    response = client.put(
        f"/api/admin/mcp/keys/{key_id}/capabilities/generation/config",
        headers=auth_headers,
        json={"config": {"image_model": "nano-banana-2", "video_model": "minimax-h3"}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["capability_configs"]["generation"]["image_model"] == "nano-banana-2"

    # 重新授权同一能力时应保留配置
    response = client.put(
        f"/api/admin/mcp/keys/{key_id}/capabilities",
        headers=auth_headers,
        json={"capability_ids": ["generation"]},
    )
    assert response.json()["capability_configs"]["generation"]["video_model"] == "minimax-h3"
    assert key


def test_mcp_image_generate_uses_configured_model(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    monkeypatch.setattr(service, "_adapter_for", lambda db, provider: FakeAdapter())

    def fake_download(task_id: str, asset_id: str, remote_url: str, max_bytes: int) -> dict:
        name = f"{asset_id}.png"
        target = storage.task_dir(task_id) / name
        target.write_bytes(PNG_BYTES)
        thumb = f"{asset_id}-thumb.jpg"
        made = storage.make_thumbnail(target, storage.task_dir(task_id) / thumb)
        return {
            "filename": name,
            "content_type": "image/png",
            "size": len(PNG_BYTES),
            "width": 2,
            "height": 2,
            "thumb_filename": thumb if made else None,
        }

    monkeypatch.setattr(service, "_download_result", fake_download)

    # 在「服务目录」侧配置该能力对外提供的模型
    response = client.put(
        "/api/admin/generation/settings",
        headers=auth_headers,
        json={"default_image_model": "nano-banana-2", "default_video_model": "minimax-h3"},
    )
    assert response.status_code == 200, response.text

    key = _create_key(client, auth_headers)

    # 传 model 企图切换模型应被忽略，仍使用服务目录配置的模型
    response = client.post(
        "/v1/capabilities/generation/image_generate",
        headers={"x-api-key": key},
        json={
            "prompt": "一只边牧",
            "model": "gpt-image-2",
            "params": {"aspectRatio": "1:1", "imageSize": "1K"},
            "wait_seconds": 60,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"] == "nano-banana-2"
    assert body["status"] == "succeeded"
    # 转存由后台循环负责；内联等待只推进状态，返回时结果可能还在 pending
    result = body["results"][0]
    assert result["remote_url"] == "https://example.com/a.png"
    # 转存可能由后台循环并发处理，轮询到 ready 为止
    for _ in range(40):
        if result["status"] == "ready":
            break
        service.download_pending_once()
        time.sleep(0.05)
        body = client.post(
            "/v1/capabilities/generation/generation_job",
            headers={"x-api-key": key},
            json={"job_id": body["job_id"]},
        ).json()
        result = body["results"][0]
    assert result["status"] == "ready"
    assert result["download_url"].startswith("http")

    listing = client.post(
        "/v1/capabilities/generation/generation_list",
        headers={"x-api-key": key},
        json={"kind": "image"},
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    # 默认不内联等待：提交请求应立即返回 queued/running，而不是等生成完成
    started = time.perf_counter()
    quick = client.post(
        "/v1/capabilities/generation/image_generate",
        headers={"x-api-key": key},
        json={"prompt": "第二张", "params": {"aspectRatio": "1:1", "imageSize": "1K"}},
    )
    elapsed = time.perf_counter() - started
    assert quick.status_code == 200, quick.text
    assert elapsed < 1.0
    assert quick.json()["job_id"]
    assert quick.json()["status"] in {"queued", "submitting", "running", "succeeded"}


def test_mcp_requires_capability(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "other", "capability_ids": ["knowledge"]},
    )
    key = response.json()["key"]
    response = client.post(
        "/v1/capabilities/generation/image_generate",
        headers={"x-api-key": key},
        json={"prompt": "x"},
    )
    assert response.status_code == 403


def test_generation_default_wait_is_zero() -> None:
    from app.capabilities.generation.provider import GenerationProvider

    assert GenerationProvider._wait_seconds(None) == 0

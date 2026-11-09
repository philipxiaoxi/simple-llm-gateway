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


def _create_key(
    client: TestClient, auth_headers: dict[str, str], capability_ids: list[str] | None = None
) -> str:
    response = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": "gen-key", "capability_ids": capability_ids or ["image_gen"]},
    )
    assert response.status_code == 201, response.text
    return response.json()["key"]


def _configure_provider(client: TestClient, auth_headers: dict[str, str], provider: str = "gsai") -> None:
    response = client.put(
        "/api/admin/generation/settings",
        headers=auth_headers,
        json={"providers": [{"provider": provider, "api_key": "test-key"}]},
    )
    assert response.status_code == 200, response.text


def test_generation_capability_exposed(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/admin/mcp/catalog", headers=auth_headers)
    assert response.status_code == 200, response.text
    ids = [item["capability_id"] for item in response.json()["items"]]
    assert {"image_gen", "video_gen"} <= set(ids)
    assert "generation" not in ids


def test_key_capability_config_roundtrip(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _create_key(client, auth_headers, ["image_gen", "video_gen"])
    keys = client.get("/api/admin/mcp/keys", headers=auth_headers).json()
    key_id = next(item["id"] for item in keys if item["name"] == "gen-key")

    response = client.put(
        f"/api/admin/mcp/keys/{key_id}/capabilities/image_gen/config",
        headers=auth_headers,
        json={"config": {"model": "nano-banana-2"}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["capability_configs"]["image_gen"]["model"] == "nano-banana-2"

    # 重新授权同一能力时应保留配置
    response = client.put(
        f"/api/admin/mcp/keys/{key_id}/capabilities",
        headers=auth_headers,
        json={"capability_ids": ["image_gen", "video_gen"]},
    )
    assert response.json()["capability_configs"]["image_gen"]["model"] == "nano-banana-2"
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

    # 仍然支持服务端默认模型：不传 model 时使用它
    _configure_provider(client, auth_headers)
    response = client.put(
        "/api/admin/generation/settings",
        headers=auth_headers,
        json={"default_image_model": "nano-banana-2", "default_video_model": "minimax-h3"},
    )
    assert response.status_code == 200, response.text

    key = _create_key(client, auth_headers)

    # 传 model 可切换模型：使用该模型自己的参数
    response = client.post(
        "/v1/capabilities/image_gen/image_generate",
        headers={"x-api-key": key},
        json={
            "prompt": "一只边牧",
            "model": "gsai:gpt-image-2",
            "params": {"aspectRatio": "1:1", "quality": "auto"},
            "wait_seconds": 60,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"] == "gpt-image-2"
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
            "/v1/capabilities/image_gen/image_job",
            headers={"x-api-key": key},
            json={"job_id": body["job_id"]},
        ).json()
        result = body["results"][0]
    assert result["status"] == "ready"
    assert result["download_url"].startswith("http")

    listing = client.post(
        "/v1/capabilities/image_gen/image_list",
        headers={"x-api-key": key},
        json={"kind": "image"},
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    # 默认不内联等待：提交请求应立即返回 queued/running，而不是等生成完成
    started = time.perf_counter()
    quick = client.post(
        "/v1/capabilities/image_gen/image_generate",
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
        "/v1/capabilities/image_gen/image_generate",
        headers={"x-api-key": key},
        json={"prompt": "x"},
    )
    assert response.status_code == 403


def test_models_endpoint_lists_params(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _create_key(client, auth_headers)
    response = client.get("/v1/capabilities/image_gen/models", headers={"x-api-key": key})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "image"
    # 测试环境没有供应商 Key，默认只返回可用模型；显式要求全部时能看到不可用原因
    fallback = client.get(
        "/v1/capabilities/image_gen/models",
        headers={"x-api-key": key},
        params={"available_only": "false"},
    )
    assert fallback.status_code == 200, fallback.text
    items = fallback.json()["models"]
    assert items
    sample = next(item for item in items if item["model"] == "gsai:nano-banana-2")
    assert sample["available"] is False
    assert sample["supports_images"] is True
    assert {param["key"] for param in sample["params"]} == {"aspectRatio", "imageSize"}


def test_unconfigured_provider_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _create_key(client, auth_headers)
    response = client.post(
        "/v1/capabilities/image_gen/image_generate",
        headers={"x-api-key": key},
        json={"prompt": "x", "model": "gsai:nano-banana-2"},
    )
    assert response.status_code == 400, response.text
    assert "未配置 API Key" in response.json()["error"]["message"]


def test_unknown_param_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    _configure_provider(client, auth_headers)
    key = _create_key(client, auth_headers)
    response = client.post(
        "/v1/capabilities/image_gen/image_generate",
        headers={"x-api-key": key},
        json={"prompt": "x", "model": "gsai:nano-banana-2", "params": {"duration": 5}},
    )
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert "不支持参数" in error["message"]
    assert error["diagnostics"][0]["allowed_params"] == ["aspectRatio", "imageSize"]


def test_default_wait_is_zero() -> None:
    from app.capabilities.generation.provider import GenerationProvider

    assert GenerationProvider._wait_seconds(None) == 0

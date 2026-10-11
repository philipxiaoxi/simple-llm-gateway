from __future__ import annotations

import io
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from PIL import Image

from app.db import get_session_factory
from app.generation import service, storage
from app.generation.base import GenerationUpdate
from app.models import GenerationAsset


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG_BYTES = _png_bytes()


class FakeAdapter:
    provider = "gsai"

    def submit(self, **kwargs) -> GenerationUpdate:  # noqa: ANN003
        return GenerationUpdate(status="running", progress=5, upstream_id="up-1")

    def poll(self, upstream_id: str) -> GenerationUpdate:
        return GenerationUpdate(
            status="succeeded",
            progress=100,
            results=["https://example.com/result.png"],
        )


def test_settings_and_catalog(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/admin/generation/settings", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["providers"][0]["provider"] == "gsai"
    assert body["providers"][0]["configured"] is False
    assert len(body["catalog"]["image"]) >= 10
    assert len(body["catalog"]["video"]) == 1
    finish = next(m for m in body["catalog"]["video"] if m["id"] == "minimax-h3")
    assert finish["supports_audios"] is True

    response = client.put(
        "/api/admin/generation/settings",
        headers=auth_headers,
        json={
            "providers": [{"provider": "gsai", "base_url": "https://grsaiapi.com", "api_key": "sk-test"}],
            "default_image_model": "nano-banana-2",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["providers"][0]["has_key"] is True
    assert response.json()["providers"][0]["configured"] is True


def test_settings_rejects_bad_base_url(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.put(
        "/api/admin/generation/settings",
        headers=auth_headers,
        json={"providers": [{"provider": "gsai", "base_url": "ftp://x"}]},
    )
    assert response.status_code == 400


def test_upload_and_delete_input(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/admin/generation/uploads",
        headers=auth_headers,
        files={"file": ("ref.png", PNG_BYTES, "image/png")},
    )
    assert response.status_code == 201, response.text
    asset = response.json()
    assert asset["kind"] == "image"
    assert asset["status"] == "ready"
    assert "url" in asset

    media = client.get(asset["url"].split("?")[0], params={"token": asset["url"].split("token=")[1]})
    assert media.status_code == 200
    assert media.content == PNG_BYTES

    assert client.delete(f"/api/admin/generation/uploads/{asset['id']}", headers=auth_headers).status_code == 204


def test_upload_rejects_unknown_type(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/admin/generation/uploads",
        headers=auth_headers,
        files={"file": ("x.txt", b"hello world", "text/plain")},
    )
    assert response.status_code == 400


def test_task_lifecycle(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    monkeypatch.setattr(service, "_adapter_for", lambda db, provider: FakeAdapter())

    response = client.post(
        "/api/admin/generation/tasks",
        headers=auth_headers,
        json={
            "kind": "image",
            "model": "nano-banana-2",
            "prompt": "一只边牧在直播",
            "params": {"aspectRatio": "1:1", "imageSize": "1K"},
        },
    )
    assert response.status_code == 201, response.text
    task_id = response.json()["id"]
    assert response.json()["status"] == "queued"

    assert service.submit_pending_once()["submitted"] == 1
    assert service.poll_running_once()["finished"] == 1

    def fake_download(task_id: str, asset_id: str, remote_url: str, max_bytes: int) -> dict:
        name = f"{asset_id}.png"
        target = storage.task_dir(task_id) / name
        target.write_bytes(PNG_BYTES)
        thumb_name = f"{asset_id}-thumb.jpg"
        made = storage.make_thumbnail(target, storage.task_dir(task_id) / thumb_name)
        return {
            "filename": name,
            "content_type": "image/png",
            "size": len(PNG_BYTES),
            "width": 2,
            "height": 2,
            "thumb_filename": thumb_name if made else None,
        }

    monkeypatch.setattr(service, "_download_result", fake_download)
    assert service.download_pending_once()["ready"] == 1

    detail = client.get(f"/api/admin/generation/tasks/{task_id}", headers=auth_headers).json()
    assert detail["status"] == "succeeded"
    assert detail["progress"] == 100
    assert len(detail["results"]) == 1
    result = detail["results"][0]
    assert result["status"] == "ready"
    media = client.get(result["url"].split("?")[0], params={"token": result["url"].split("token=")[1]})
    assert media.status_code == 200
    assert result.get("thumb_url")
    parsed = urlparse(result["thumb_url"])
    thumb = client.get(parsed.path, params={"token": parse_qs(parsed.query)["token"][0], "thumb": "1"})
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/jpeg")

    listing = client.get("/api/admin/generation/tasks?kind=image", headers=auth_headers).json()
    assert listing["total"] == 1
    assert listing["counts"].get("succeeded") == 1

    assert client.delete(f"/api/admin/generation/tasks/{task_id}", headers=auth_headers).status_code == 204
    assert not (storage.root() / task_id).exists()


def test_task_validation(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/admin/generation/tasks",
        headers=auth_headers,
        json={"kind": "image", "model": "minimax-h3", "prompt": "x", "params": {}},
    )
    assert response.status_code == 400

    response = client.post(
        "/api/admin/generation/tasks",
        headers=auth_headers,
        json={"kind": "video", "model": "minimax-h3", "prompt": "x", "params": {"aspectRatio": "portrait"}},
    )
    assert response.status_code == 400

    response = client.post(
        "/api/admin/generation/tasks",
        headers=auth_headers,
        json={"kind": "video", "model": "minimax-h3", "prompt": "", "params": {"aspectRatio": "portrait", "resolution": "480p", "duration": 5}},
    )
    assert response.status_code == 400


def test_asset_token_required(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/admin/generation/uploads",
        headers=auth_headers,
        files={"file": ("ref.png", PNG_BYTES, "image/png")},
    )
    asset_id = response.json()["id"]
    assert client.get(f"/api/admin/generation/assets/{asset_id}").status_code == 401
    session = get_session_factory()()
    try:
        assert session.get(GenerationAsset, asset_id) is not None
    finally:
        session.close()

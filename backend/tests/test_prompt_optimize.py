from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.capabilities.generation import prompt_optimize
from app.capabilities.generation.prompt_optimize import PromptOptimizeError, select_mode, validate_prompt
from app.clock import utcnow
from app.db import get_session_factory
from app.models import UpstreamAccount


def _sample(mode: str, duration: str = "6.00") -> str:
    prefix = prompt_optimize.alignment_prefix(mode, duration)
    body = (
        "integrated_multimodal_description: [Shot 1] Live-action, a baker opens the shutters.\n\n"
        "overall_soundscape: Wooden shutters scrape over a quiet street.\n\n"
        "non_diegetic_music: Sparse piano notes at a slow tempo."
    )
    return f"{prefix}\n\n{body}".strip()


def test_select_mode_from_image_count() -> None:
    assert select_mode(0) == "t2va"
    assert select_mode(1) == "i2va"
    assert select_mode(2) == "fl2va"
    assert select_mode(3) == "fl2va"
    assert select_mode(0, "l2va") == "l2va"


def test_validate_prompt_requires_fields_and_alignment() -> None:
    assert "integrated_multimodal_description" in validate_prompt(_sample("t2va"), "t2va", "6.00")
    with pytest.raises(PromptOptimizeError):
        validate_prompt("integrated_multimodal_description: x", "i2va", "6.00")
    with pytest.raises(PromptOptimizeError):
        validate_prompt("overall_soundscape: x\nintegrated_multimodal_description: y", "t2va", "6.00")


def test_settings_persist_optimizer_and_reject_missing_account(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/admin/generation/settings", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["prompt_optimize_enabled"] is False

    rejected = client.put(
        "/api/admin/generation/settings",
        headers=auth_headers,
        json={"prompt_optimize_enabled": True, "prompt_optimize_account_id": 99, "prompt_optimize_model": "gpt"},
    )
    assert rejected.status_code == 409
    assert client.get("/api/admin/generation/settings", headers=auth_headers).json()["prompt_optimize_enabled"] is False


def test_optimize_replaces_prompt_and_keeps_original_on_failure(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    session = get_session_factory()()
    session.add(
        UpstreamAccount(
            name="优化账号",
            provider="openai",
            auth_type="api_key",
            base_url="https://api.example.com/v1",
            status="active",
            models_json='[{"id":"writer"}]',
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    session.commit()
    account_id = session.query(UpstreamAccount).one().id
    session.close()

    saved = client.put(
        "/api/admin/generation/settings",
        headers=auth_headers,
        json={"prompt_optimize_enabled": True, "prompt_optimize_account_id": account_id, "prompt_optimize_model": "writer"},
    )
    assert saved.status_code == 200, saved.text

    async def fake_call(*_args, **_kwargs):
        return {"choices": [{"message": {"content": _sample("i2va")}}]}

    async def fake_prepare(*_args, **_kwargs):
        return {"api_key": "test"}

    monkeypatch.setattr(prompt_optimize, "call_chat", fake_call)
    monkeypatch.setattr(prompt_optimize, "prepare_credential", fake_prepare)
    ok = client.post(
        "/api/admin/generation/prompt-optimize",
        headers=auth_headers,
        json={"prompt": "雨中的人", "duration_seconds": 6, "image_count": 1},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["mode"] == "i2va"

    async def last_frame_call(*_args, **_kwargs):
        return {"choices": [{"message": {"content": _sample("l2va")}}]}

    monkeypatch.setattr(prompt_optimize, "call_chat", last_frame_call)
    chosen = client.post(
        "/api/admin/generation/prompt-optimize",
        headers=auth_headers,
        json={"prompt": "落到最后一帧", "duration_seconds": 6, "image_count": 0, "mode": "l2va"},
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["mode"] == "l2va"
    assert "api_key" not in ok.text

    async def fail_call(*_args, **_kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(prompt_optimize, "call_chat", fail_call)
    failed = client.post(
        "/api/admin/generation/prompt-optimize",
        headers=auth_headers,
        json={"prompt": "保留我", "duration_seconds": 6, "image_count": 0},
    )
    assert failed.status_code == 502
    assert "保留我" not in failed.text

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _config(voice_service, stt_ready: bool = True, llm_ready: bool = True):
    return voice_service.VoiceConfig(
        stt_account=voice_service.VoiceAccount(
            base_url="https://stt.example.com/v1",
            api_key="stt-key",
        ) if stt_ready else voice_service.VoiceAccount(),
        stt_model="whisper-1",
        llm_account=voice_service.VoiceAccount(
            base_url="https://llm.example.com/v1",
            api_key="llm-key",
        ) if llm_ready else voice_service.VoiceAccount(),
        llm_model="gpt-4o-mini",
    )


def test_create_list_and_join_room(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post("/api/admin/voice/rooms", headers=auth_headers, json={"name": "我的电脑"})
    assert created.status_code == 200, created.text
    room = created.json()
    assert room["code"]
    assert room["status"] == "active"
    assert room["name"] == "我的电脑"
    assert room["mobile_url"]
    assert "voice/mobile" in room["mobile_url"]
    assert room["online_connections"] == 0

    listed = client.get("/api/admin/voice/rooms", headers=auth_headers)
    assert listed.status_code == 200
    codes = [item["code"] for item in listed.json()]
    assert room["code"] in codes

    joined = client.get(f"/api/voice/rooms/{room['code']}")
    assert joined.status_code == 200
    assert joined.json()["status"] == "active"


def test_join_missing_room_returns_404(client: TestClient) -> None:
    response = client.get("/api/voice/rooms/NOTEXIST")
    assert response.status_code == 404


def test_close_room_blocks_join_and_transcribe(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post("/api/admin/voice/rooms", headers=auth_headers, json={"name": "关闭测试"})
    room = created.json()

    closed = client.patch(
        f"/api/admin/voice/rooms/{room['id']}",
        headers=auth_headers,
        json={"status": "closed"},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"

    joined = client.get(f"/api/voice/rooms/{room['code']}")
    assert joined.status_code == 403

    transcribe = client.post(
        "/api/voice/transcribe",
        files=[("audio", ("rec.webm", b"fake-audio-bytes", "audio/webm"))],
        data={"room": room["code"]},
    )
    assert transcribe.status_code == 403


def test_delete_room(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post("/api/admin/voice/rooms", headers=auth_headers, json={"name": "删除测试"})
    room = created.json()
    deleted = client.delete(f"/api/admin/voice/rooms/{room['id']}", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True
    assert client.get(f"/api/voice/rooms/{room['code']}").status_code == 404


def test_transcribe_uses_stt_and_llm(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    async def fake_stt(_config, _audio_bytes, _filename) -> str:
        return "我想优化这段语音文字"

    async def fake_polish(_config, raw_text: str) -> str:
        return f"【优化】{raw_text}"

    from app.services import voice as voice_service

    monkeypatch.setattr(voice_service, "_stt_to_text", fake_stt)
    monkeypatch.setattr(voice_service, "_polish_text", fake_polish)
    monkeypatch.setattr(voice_service, "load_voice_config", lambda db: _config(voice_service))

    created = client.post("/api/admin/voice/rooms", headers=auth_headers, json={"name": "转写测试"})
    room = created.json()

    response = client.post(
        "/api/voice/transcribe",
        files=[("audio", ("rec.webm", b"fake-audio-bytes", "audio/webm"))],
        data={"room": room["code"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["raw_text"] == "我想优化这段语音文字"
    assert body["text"] == "【优化】我想优化这段语音文字"
    assert body["seq"] == 1
    assert body["stt_model"] == "whisper-1"
    assert body["llm_model"] == "gpt-4o-mini"

    listed = client.get("/api/admin/voice/rooms", headers=auth_headers)
    assert listed.status_code == 200
    target = next(item for item in listed.json() if item["code"] == room["code"])
    assert target["recent_messages"]
    assert target["recent_messages"][0]["text"] == "【优化】我想优化这段语音文字"
    assert target["recent_logs"]
    assert any(log["kind"] == "transcribed" for log in target["recent_logs"])


def test_transcribe_without_stt_config_returns_502(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    from app.services import voice as voice_service

    monkeypatch.setattr(voice_service, "load_voice_config", lambda db: _config(voice_service, stt_ready=False))

    created = client.post("/api/admin/voice/rooms", headers=auth_headers, json={"name": "配置测试"})
    room = created.json()

    response = client.post(
        "/api/voice/transcribe",
        files=[("audio", ("rec.webm", b"fake-audio-bytes", "audio/webm"))],
        data={"room": room["code"]},
    )
    assert response.status_code == 502
    assert "未配置" in response.json()["detail"]

    listed = client.get("/api/admin/voice/rooms", headers=auth_headers)
    target = next(item for item in listed.json() if item["code"] == room["code"])
    assert any(log["kind"] == "error" for log in target["recent_logs"])


def test_websocket_receives_transcribed_text_and_ack(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    async def fake_stt(_config, _audio_bytes, _filename) -> str:
        return "语音转写的原始文本"

    async def fake_polish(_config, raw_text: str) -> str:
        return raw_text

    from app.services import voice as voice_service

    monkeypatch.setattr(voice_service, "_stt_to_text", fake_stt)
    monkeypatch.setattr(voice_service, "_polish_text", fake_polish)
    monkeypatch.setattr(voice_service, "load_voice_config", lambda db: _config(voice_service))

    created = client.post("/api/admin/voice/rooms", headers=auth_headers, json={"name": "WS 测试"})
    room = created.json()

    with client.websocket_connect(f"/api/voice/ws/{room['code']}") as websocket:
        hello = websocket.receive_json()
        assert hello["type"] == "connected"
        assert hello["room"] == room["code"]

        response = client.post(
            "/api/voice/transcribe",
            files=[("audio", ("rec.webm", b"fake-audio-bytes", "audio/webm"))],
            data={"room": room["code"]},
        )
        assert response.status_code == 200

        frame = websocket.receive_json()
        assert frame["type"] == "text"
        assert frame["text"] == "语音转写的原始文本"
        assert frame["seq"] == 1

        websocket.send_json({"type": "ack", "seq": 1, "ok": True, "method": "clipboard"})

    listed = client.get("/api/admin/voice/rooms", headers=auth_headers)
    target = next(item for item in listed.json() if item["code"] == room["code"])
    assert target["recent_messages"][0]["acked_count"] == 1
    assert any(log["kind"] == "acked" for log in target["recent_logs"])


def test_websocket_closed_room_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    import starlette.websockets

    created = client.post("/api/admin/voice/rooms", headers=auth_headers, json={"name": "WS 关闭测试"})
    room = created.json()
    client.patch(
        f"/api/admin/voice/rooms/{room['id']}",
        headers=auth_headers,
        json={"status": "closed"},
    )
    with pytest.raises(starlette.websockets.WebSocketDisconnect):
        with client.websocket_connect(f"/api/voice/ws/{room['code']}") as websocket:
            websocket.receive()


def test_save_settings_requires_valid_account(client: TestClient, auth_headers: dict[str, str]) -> None:
    settings = client.get("/api/admin/voice/settings", headers=auth_headers)
    assert settings.status_code == 200
    payload = settings.json()
    assert "stt_accounts" in payload
    assert "llm_accounts" in payload

    response = client.put(
        "/api/admin/voice/settings",
        headers=auth_headers,
        json={"stt_account_id": 99999, "llm_account_id": None},
    )
    assert response.status_code == 400 or response.status_code == 422

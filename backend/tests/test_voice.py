from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


def _config(voice_service, stt_ready: bool = True, llm_ready: bool = True):
    return voice_service.VoiceConfig(
        stt_api_key="stt-key" if stt_ready else "",
        stt_ws_url="wss://dashscope.aliyuncs.com/api-ws/v1/inference",
        stt_model="qwen-audio-3.0-asr-flash-streaming",
        llm_account=voice_service.VoiceAccount(
            base_url="https://llm.example.com/v1",
            api_key="llm-key",
        ) if llm_ready else voice_service.VoiceAccount(),
        llm_model="gpt-4o-mini",
    )


class FakeAsrSession:
    def __init__(self, *args, **kwargs) -> None:
        self.audio: list[bytes] = []
        self.finished = False

    async def start(self) -> None:
        pass

    async def send_audio(self, pcm: bytes) -> None:
        self.audio.append(pcm)

    async def finish(self) -> None:
        self.finished = True

    async def close(self) -> None:
        pass

    async def results(self):
        yield {"text": "你好", "sentence_end": False}
        yield {"text": "你好世界", "sentence_end": True}
        yield {"text": "今天天气不错", "sentence_end": True}


def test_create_list_and_join_room(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post("/api/admin/voice/rooms", headers=auth_headers, json={"name": "我的电脑"})
    assert created.status_code == 200, created.text
    room = created.json()
    assert room["code"]
    assert room["status"] == "active"
    assert room["name"] == "我的电脑"
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


def test_close_room_blocks_join(client: TestClient, auth_headers: dict[str, str]) -> None:
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


def test_delete_room(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post("/api/admin/voice/rooms", headers=auth_headers, json={"name": "删除测试"})
    room = created.json()
    deleted = client.delete(f"/api/admin/voice/rooms/{room['id']}", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True
    assert client.get(f"/api/voice/rooms/{room['code']}").status_code == 404


def test_asr_streams_transcript_and_optimized(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    from app.services import voice as voice_service
    from app.routers import voice as voice_router

    async def fake_polish(_config, raw_text: str) -> str:
        return f"【优化】{raw_text}"

    monkeypatch.setattr(voice_router, "AliyunAsrSession", FakeAsrSession)
    monkeypatch.setattr(voice_router, "load_voice_config", lambda db: _config(voice_service))
    monkeypatch.setattr(voice_router, "polish_text", fake_polish)

    created = client.post("/api/admin/voice/rooms", headers=auth_headers, json={"name": "ASR 测试"})
    room = created.json()

    with client.websocket_connect(f"/api/voice/ws/{room['code']}") as desktop:
        hello = desktop.receive_json()
        assert hello["type"] == "connected"

        with client.websocket_connect(f"/api/voice/asr/{room['code']}") as phone:
            phone.send_bytes(b"\x00\x00" * 100)
            phone.send_text(json.dumps({"type": "stop"}))

            partial = phone.receive_json()
            assert partial["type"] == "partial"
            assert partial["text"] == "你好"

            sentence1 = phone.receive_json()
            assert sentence1["type"] == "sentence"
            assert sentence1["text"] == "你好世界"
            sentence2 = phone.receive_json()
            assert sentence2["type"] == "sentence"
            assert sentence2["text"] == "今天天气不错"

            optimized = phone.receive_json()
            assert optimized["type"] == "optimized"
            assert optimized["text"] == "【优化】你好世界今天天气不错"
            assert optimized["delivered"] == 1

        transcript1 = desktop.receive_json()
        assert transcript1["type"] == "transcript"
        assert transcript1["text"] == "你好世界"
        transcript2 = desktop.receive_json()
        assert transcript2["type"] == "transcript"
        assert transcript2["text"] == "今天天气不错"
        opt = desktop.receive_json()
        assert opt["type"] == "optimized"
        assert opt["seq"] == 1
        assert opt["text"] == "【优化】你好世界今天天气不错"

    listed = client.get("/api/admin/voice/rooms", headers=auth_headers)
    target = next(item for item in listed.json() if item["code"] == room["code"])
    assert target["recent_messages"]
    assert target["recent_messages"][0]["raw_text"] == "你好世界今天天气不错"
    assert target["recent_messages"][0]["text"] == "【优化】你好世界今天天气不错"
    assert any(log["kind"] == "transcribed" for log in target["recent_logs"])
    assert any(log["kind"] == "sent" for log in target["recent_logs"])


def test_asr_without_stt_config_reports_error(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    from app.services import voice as voice_service
    from app.routers import voice as voice_router

    monkeypatch.setattr(voice_router, "load_voice_config", lambda db: _config(voice_service, stt_ready=False))

    created = client.post("/api/admin/voice/rooms", headers=auth_headers, json={"name": "配置测试"})
    room = created.json()

    with client.websocket_connect(f"/api/voice/asr/{room['code']}") as phone:
        frame = phone.receive_json()
        assert frame["type"] == "error"
        assert "未配置" in frame["message"]


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


def test_save_settings(client: TestClient, auth_headers: dict[str, str]) -> None:
    settings = client.get("/api/admin/voice/settings", headers=auth_headers)
    assert settings.status_code == 200
    payload = settings.json()
    assert "stt_configured" in payload
    assert "llm_accounts" in payload
    assert payload["stt_model"] == "qwen-audio-3.0-asr-flash-streaming"

    saved = client.put(
        "/api/admin/voice/settings",
        headers=auth_headers,
        json={"stt_api_key": "sk-test-key", "llm_model": "gpt-4o-mini"},
    )
    assert saved.status_code == 200
    assert saved.json()["stt_configured"] is True

    response = client.put(
        "/api/admin/voice/settings",
        headers=auth_headers,
        json={"llm_account_id": 99999},
    )
    assert response.status_code == 400

"""语音房 WebSocket 端点测试。

用 starlette 的 TestClient.websocket_connect 驱动真实端点；
阿里云那一侧用假 WebSocket 顶掉，避免测试依赖外网。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.db import get_session_factory
from app.models import VoiceEvent, VoiceSegment, VoiceRoom
from app.routers.voice_rooms import mint_room_token
from app.services import voice_asr
from app.services.voice_hub import voice_hub
from app.services.voice_session import voice_session_manager


class FakeAsrSocket:
    """假的 DashScope 连接：收到 run-task 后立刻回 task-started，收到 finish-task 回 task-finished。"""

    def __init__(self, final_text: str = "你好，我们明天见。") -> None:
        self.final_text = final_text
        self.sent_text: list[dict[str, Any]] = []
        self.sent_bytes: list[bytes] = []
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._started = asyncio.Event()
        self.closed = False

    async def send(self, payload: Any) -> None:
        if isinstance(payload, (bytes, bytearray)):
            self.sent_bytes.append(bytes(payload))
            return
        frame = json.loads(payload)
        self.sent_text.append(frame)
        action = frame.get("header", {}).get("action")
        if action == "run-task":
            await self._queue.put(json.dumps({"header": {"event": "task-started"}, "payload": {}}))
            self._started.set()
        elif action == "finish-task":
            await self._queue.put(
                json.dumps(
                    {
                        "header": {"event": "result-generated"},
                        "payload": {
                            "output": {
                                "sentence": {
                                    "text": self.final_text,
                                    "sentence_end": True,
                                    "begin_time": 100,
                                    "end_time": 1500,
                                }
                            },
                            "usage": {"duration": 2},
                        },
                    }
                )
            )
            await self._queue.put(json.dumps({"header": {"event": "task-finished"}, "payload": {}}))

    def __aiter__(self) -> "FakeAsrSocket":
        return self

    async def __anext__(self) -> str:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self) -> None:
        self.closed = True


def _wait_for(ws, kind: str, limit: int = 20) -> dict:
    """按序读取消息直到拿到指定类型（房间状态等通知会插进来）。"""
    for _ in range(limit):
        message = ws.receive_json()
        if message.get("type") == kind:
            return message
    raise AssertionError(f"未在 {limit} 条消息内收到 {kind}")


@pytest.fixture()
def asr_key(monkeypatch) -> None:
    monkeypatch.setenv("ALIYUN_DASHSCOPE_API_KEY", "sk-ws-unit-test")
    from app.config import reset_settings

    reset_settings()
    yield
    reset_settings()


def _make_room(client: TestClient, headers: dict[str, str], **overrides) -> dict:
    payload = {"name": "书房", "polishMode": "off", **overrides}
    response = client.post("/api/admin/voice/rooms", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_phone_socket_rejects_bad_token(client: TestClient, auth_headers: dict[str, str]) -> None:
    room = _make_room(client, auth_headers)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/api/voice/rooms/{room['roomId']}/phone?token=garbage") as ws:
            ws.receive_text()
    assert excinfo.value.code == 1008


def test_phone_socket_rejects_token_of_other_room(client: TestClient, auth_headers: dict[str, str]) -> None:
    room = _make_room(client, auth_headers)
    other = _make_room(client, auth_headers, name="另一个")
    token = mint_room_token(room_id=other["roomId"], role="phone", client_uid="p1", ttl_seconds=60)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/api/voice/rooms/{room['roomId']}/phone?token={token}") as ws:
            ws.receive_text()
    assert excinfo.value.code == 1008


def test_phone_socket_rejects_desktop_token(client: TestClient, auth_headers: dict[str, str]) -> None:
    room = _make_room(client, auth_headers)
    token = mint_room_token(room_id=room["roomId"], role="desktop", client_uid="d1", ttl_seconds=60)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/api/voice/rooms/{room['roomId']}/phone?token={token}") as ws:
            ws.receive_text()
    assert excinfo.value.code == 1008


def test_phone_socket_requires_hello_first(client: TestClient, auth_headers: dict[str, str]) -> None:
    room = _make_room(client, auth_headers)
    token = mint_room_token(room_id=room["roomId"], role="phone", client_uid="p1", ttl_seconds=60)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/api/voice/rooms/{room['roomId']}/phone?token={token}") as ws:
            ws.send_text(json.dumps({"type": "start"}))
            ws.receive_text()
    assert excinfo.value.code == 1008


def test_phone_socket_hello_returns_ready(client: TestClient, auth_headers: dict[str, str]) -> None:
    room = _make_room(client, auth_headers)
    token = mint_room_token(room_id=room["roomId"], role="phone", client_uid="p1", ttl_seconds=60)
    try:
        with client.websocket_connect(f"/api/voice/rooms/{room['roomId']}/phone?token={token}") as ws:
            ws.send_text(json.dumps({"type": "hello", "clientUid": "p1", "name": "小飞的 iPhone"}))
            ready = ws.receive_json()
            assert ready["type"] == "ready"
            assert ready["room"]["roomId"] == room["roomId"]
            assert ready["asrModel"] == room["asrModel"]
            assert ready["roomState"]["phones"] == 1

            ws.send_text(json.dumps({"type": "ping"}))
            assert _wait_for(ws, "pong")["type"] == "pong"
    finally:
        voice_hub._rooms.clear()
        voice_session_manager._rooms.clear()


def test_phone_start_without_api_key_reports_error(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    monkeypatch.setenv("ALIYUN_DASHSCOPE_API_KEY", "")
    from app.config import reset_settings

    reset_settings()
    room = _make_room(client, auth_headers)
    token = mint_room_token(room_id=room["roomId"], role="phone", client_uid="p1", ttl_seconds=60)
    try:
        with client.websocket_connect(f"/api/voice/rooms/{room['roomId']}/phone?token={token}") as ws:
            ws.send_text(json.dumps({"type": "hello", "clientUid": "p1", "name": "手机"}))
            assert _wait_for(ws, "ready")["type"] == "ready"
            ws.send_text(json.dumps({"type": "start", "recordingId": "rec-1"}))
            error = _wait_for(ws, "error")
            assert error["code"] == "ASR_UNAVAILABLE"
    finally:
        voice_hub._rooms.clear()
        voice_session_manager._rooms.clear()
        reset_settings()


def test_full_audio_round_trip(client: TestClient, auth_headers: dict[str, str], asr_key, monkeypatch) -> None:
    """手机发二进制音频 → 假 ASR 回终稿 → 手机收到 asr.final，且段落与事件都落库。"""
    fake = FakeAsrSocket("你好，我们明天见。")

    async def fake_connect(*_args, **_kwargs):
        return fake

    monkeypatch.setattr(voice_asr, "connect", fake_connect)
    room = _make_room(client, auth_headers)

    desktop_token = mint_room_token(room_id=room["roomId"], role="desktop", client_uid="d1", ttl_seconds=60)
    phone_token = mint_room_token(room_id=room["roomId"], role="phone", client_uid="p1", ttl_seconds=60)

    try:
        with client.websocket_connect("/api/voice/desktop/connect") as desktop:
            desktop.send_text(
                json.dumps(
                    {
                        "type": "register",
                        "roomId": room["roomId"],
                        "token": desktop_token,
                        "clientUid": "d1",
                        "name": "MacBook Pro",
                        "insertMode": True,
                    }
                )
            )
            registered = _wait_for(desktop, "registered")
            assert registered["type"] == "registered"

            with client.websocket_connect(f"/api/voice/rooms/{room['roomId']}/phone?token={phone_token}") as phone:
                phone.send_text(json.dumps({"type": "hello", "clientUid": "p1", "name": "小飞的 iPhone"}))
                assert phone.receive_json()["type"] == "ready"

                phone.send_text(json.dumps({"type": "start", "recordingId": "rec-1"}))
                assert _wait_for(phone, "session.started")["type"] == "session.started"

                phone.send_bytes(b"\x00\x00" * 1600)

                phone.send_text(json.dumps({"type": "stop"}))
                final = _wait_for(phone, "asr.final")
                assert final["text"] == "你好，我们明天见。"
                seg_id = final["segId"]

                # 桌面端应当收到同一段的快照
                snapshot = _wait_for(desktop, "segment.snapshot")
                assert snapshot["segId"] == seg_id
                assert snapshot["rev"] == 1
                desktop.send_text(
                    json.dumps(
                        {
                            "type": "segment.ack",
                            "segId": seg_id,
                            "rev": 1,
                            "ok": True,
                            "action": "inserted",
                            "chars": 9,
                            "text": final["text"],
                        }
                    )
                )
                # 手机会收到回执
                ack = _wait_for(phone, "segment.ack")
                assert ack["ok"] is True

        db = get_session_factory()()
        try:
            rows = db.query(VoiceSegment).all()
            assert len(rows) == 1
            assert rows[0].raw_text == "你好，我们明天见。"
            assert rows[0].rev == 1
            kinds = {row.kind for row in db.query(VoiceEvent).all()}
            assert {"room.join", "session.start", "asr.final", "segment.send", "segment.ack"} <= kinds
            room_row = db.query(VoiceRoom).one()
            assert room_row.room_id == room["roomId"]
        finally:
            db.close()
    finally:
        voice_hub._rooms.clear()
        voice_session_manager._rooms.clear()


def test_desktop_socket_rejects_bad_register(client: TestClient, auth_headers: dict[str, str]) -> None:
    room = _make_room(client, auth_headers)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/api/voice/desktop/connect") as ws:
            ws.send_text(json.dumps({"type": "register", "roomId": room["roomId"], "token": "bad"}))
            ws.receive_text()
    assert excinfo.value.code == 1008


def test_desktop_socket_requires_register_first(client: TestClient, auth_headers: dict[str, str]) -> None:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/api/voice/desktop/connect") as ws:
            ws.send_text(json.dumps({"type": "ping"}))
            ws.receive_text()
    assert excinfo.value.code == 1008


def test_public_join_flow(client: TestClient, auth_headers: dict[str, str]) -> None:
    room = _make_room(client, auth_headers, pin="2468")

    lookup = client.get(f"/api/voice/rooms/lookup?code={room['joinCode']}")
    assert lookup.status_code == 200
    assert lookup.json() == {"roomId": room["roomId"], "name": "书房", "requirePin": True}

    assert client.get("/api/voice/rooms/lookup?code=000000").status_code == 404

    wrong = client.post(
        f"/api/voice/rooms/{room['roomId']}/join",
        json={"clientUid": "p1", "name": "手机", "pin": "0000"},
    )
    assert wrong.status_code == 403

    ok = client.post(
        f"/api/voice/rooms/{room['roomId']}/join",
        json={"clientUid": "p1", "name": "手机", "pin": "2468"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["token"]
    assert body["room"]["roomId"] == room["roomId"]
    assert body["wsPath"] == f"/api/voice/rooms/{room['roomId']}/phone"

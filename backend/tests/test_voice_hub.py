"""房间广播注册表单测（对标 tests/test_local_agent_relay.py 的 FakeWebSocket 手法）。"""

from __future__ import annotations

import asyncio

import pytest

from app.services.voice_hub import ROLE_DESKTOP, ROLE_PHONE, VoiceConnection, VoiceHub


class FakeWebSocket:
    def __init__(self, fail: bool = False) -> None:
        self.json_frames: list[dict] = []
        self.byte_frames: list[bytes] = []
        self.fail = fail
        self.closed: list[tuple[int, str]] = []

    async def send_text(self, payload: str) -> None:
        if self.fail:
            raise RuntimeError("连接已断开")
        import json

        self.json_frames.append(json.loads(payload))

    async def send_bytes(self, payload: bytes) -> None:
        if self.fail:
            raise RuntimeError("连接已断开")
        self.byte_frames.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


def _conn(room: str, role: str, uid: str, *, fail: bool = False, name: str = "") -> VoiceConnection:
    return VoiceConnection(
        room_id=room, role=role, client_uid=uid, websocket=FakeWebSocket(fail=fail), name=name or uid
    )


@pytest.mark.asyncio
async def test_register_and_unregister() -> None:
    hub = VoiceHub()
    phone = _conn("r1", ROLE_PHONE, "p1")
    desktop = _conn("r1", ROLE_DESKTOP, "d1")
    hub.register(phone)
    hub.register(desktop)
    assert hub.is_room_online("r1")
    assert hub.room_state("r1") == {"busy": False, "phones": 1, "desktops": 1}

    hub.unregister(phone)
    assert hub.room_state("r1") == {"busy": False, "phones": 0, "desktops": 1}
    hub.unregister(desktop)
    assert not hub.is_room_online("r1")
    assert hub.room_state("r1")["desktops"] == 0


@pytest.mark.asyncio
async def test_broadcast_only_reaches_desktops() -> None:
    hub = VoiceHub()
    phone = _conn("r1", ROLE_PHONE, "p1")
    desktop = _conn("r1", ROLE_DESKTOP, "d1")
    other = _conn("r2", ROLE_DESKTOP, "d2")
    for item in (phone, desktop, other):
        hub.register(item)

    sent = await hub.broadcast_snapshot("r1", seg_uid="s1", seq=1, rev=1, text="你好", final=False)
    assert sent == 1
    assert len(desktop.websocket.json_frames) == 1
    assert phone.websocket.json_frames == []
    assert other.websocket.json_frames == []
    frame = desktop.websocket.json_frames[0]
    assert frame["type"] == "segment.snapshot"
    assert frame["text"] == "你好"
    assert frame["roomBusy"] is False
    assert "ts" in frame


@pytest.mark.asyncio
async def test_broadcast_to_phone_only() -> None:
    hub = VoiceHub()
    phone = _conn("r1", ROLE_PHONE, "p1")
    desktop = _conn("r1", ROLE_DESKTOP, "d1")
    hub.register(phone)
    hub.register(desktop)

    sent = await hub.broadcast_to_phone("r1", {"type": "asr.partial", "text": "你好"})
    assert sent == 1
    assert len(phone.websocket.json_frames) == 1
    assert desktop.websocket.json_frames == []


@pytest.mark.asyncio
async def test_broken_connection_is_skipped_not_fatal() -> None:
    hub = VoiceHub()
    broken = _conn("r1", ROLE_DESKTOP, "bad", fail=True)
    healthy = _conn("r1", ROLE_DESKTOP, "ok")
    hub.register(broken)
    hub.register(healthy)

    sent = await hub.broadcast("r1", {"type": "x"})
    assert sent == 1
    assert broken.connected is False
    assert len(healthy.websocket.json_frames) == 1


@pytest.mark.asyncio
async def test_online_clients_dedupes_by_uid() -> None:
    hub = VoiceHub()
    hub.register(_conn("r1", ROLE_PHONE, "same", name="手机"))
    hub.register(_conn("r1", ROLE_PHONE, "same", name="手机"))
    hub.register(_conn("r1", ROLE_DESKTOP, "d1"))
    clients = hub.online_clients("r1")
    assert len(clients) == 2
    assert {item["clientUid"] for item in clients} == {"same", "d1"}


@pytest.mark.asyncio
async def test_busy_flag_in_room_state() -> None:
    hub = VoiceHub()
    hub.register(_conn("r1", ROLE_PHONE, "p1"))
    hub.set_busy("r1", True)
    assert hub.room_state("r1")["busy"] is True
    assert hub.is_busy("r1") is True
    hub.set_busy("r1", False)
    assert hub.is_busy("r1") is False


@pytest.mark.asyncio
async def test_send_lock_serialises_concurrent_sends() -> None:
    """并发 send_json 不能交错——这是 local_agent_relay 已经踩过的坑。"""

    class SlowSocket(FakeWebSocket):
        def __init__(self) -> None:
            super().__init__()
            self.concurrent = 0
            self.max_concurrent = 0

        async def send_text(self, payload: str) -> None:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            await asyncio.sleep(0.01)
            self.concurrent -= 1
            await super().send_text(payload)

    socket = SlowSocket()
    conn = VoiceConnection(room_id="r1", role=ROLE_PHONE, client_uid="p1", websocket=socket)
    await asyncio.gather(*(conn.send_json({"type": "x", "i": i}) for i in range(8)))
    assert socket.max_concurrent == 1
    assert len(socket.json_frames) == 8


@pytest.mark.asyncio
async def test_close_room_closes_everyone() -> None:
    hub = VoiceHub()
    first = _conn("r1", ROLE_PHONE, "p1")
    second = _conn("r1", ROLE_DESKTOP, "d1")
    hub.register(first)
    hub.register(second)
    hub.set_busy("r1", True)

    await hub.close_room("r1")
    assert first.websocket.closed == [(1008, "房间已关闭")]
    assert second.websocket.closed == [(1008, "房间已关闭")]
    assert hub.is_room_online("r1") is False
    assert hub.is_busy("r1") is False


@pytest.mark.asyncio
async def test_broadcast_to_empty_room_is_safe() -> None:
    hub = VoiceHub()
    assert await hub.broadcast("nope", {"type": "x"}) == 0
    assert hub.room_state("nope") == {"busy": False, "phones": 0, "desktops": 0}

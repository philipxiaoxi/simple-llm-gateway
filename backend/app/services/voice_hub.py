"""语音房间连接注册表与广播。

对标 app/services/local_agent_relay.py 的写法：每条连接一把 send_lock，
断线只影响自己，广播失败不打断其它接收方。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

ROLE_PHONE = "phone"
ROLE_DESKTOP = "desktop"


@dataclass
class VoiceConnection:
    room_id: str
    role: str
    client_uid: str
    websocket: WebSocket
    name: str = ""
    connected: bool = True
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send_json(self, payload: dict[str, Any]) -> bool:
        if not self.connected:
            return False
        try:
            async with self.send_lock:
                await self.websocket.send_text(json.dumps(payload, ensure_ascii=False))
            return True
        except Exception:
            self.connected = False
            return False

    async def send_bytes(self, payload: bytes) -> bool:
        if not self.connected:
            return False
        try:
            async with self.send_lock:
                await self.websocket.send_bytes(payload)
            return True
        except Exception:
            self.connected = False
            return False


class VoiceHub:
    """按房间维护在线连接，并负责下发格式化好的消息。"""

    def __init__(self) -> None:
        self._rooms: dict[str, list[VoiceConnection]] = {}
        self._busy: dict[str, bool] = {}

    # ---------- 注册表 ----------

    def register(self, connection: VoiceConnection) -> None:
        self._rooms.setdefault(connection.room_id, []).append(connection)

    def unregister(self, connection: VoiceConnection) -> None:
        connection.connected = False
        members = self._rooms.get(connection.room_id)
        if not members:
            return
        self._rooms[connection.room_id] = [item for item in members if item is not connection]
        if not self._rooms[connection.room_id]:
            self._rooms.pop(connection.room_id, None)
            self._busy.pop(connection.room_id, None)

    def members(self, room_id: str) -> list[VoiceConnection]:
        return list(self._rooms.get(room_id, ()))

    def online_clients(self, room_id: str) -> list[dict[str, Any]]:
        """同一 client_uid 去重（手机切页面会短暂并存两条连接）。"""
        seen: dict[str, dict[str, Any]] = {}
        for item in self._rooms.get(room_id, ()):
            seen[item.client_uid] = {
                "clientUid": item.client_uid,
                "role": item.role,
                "name": item.name,
            }
        return list(seen.values())

    def is_room_online(self, room_id: str) -> bool:
        return bool(self._rooms.get(room_id))

    def online_room_ids(self) -> list[str]:
        return list(self._rooms)

    def room_state(self, room_id: str) -> dict[str, Any]:
        members = self._rooms.get(room_id, ())
        phones = {item.client_uid for item in members if item.role == ROLE_PHONE}
        desktops = {item.client_uid for item in members if item.role == ROLE_DESKTOP}
        return {"busy": self.is_busy(room_id), "phones": len(phones), "desktops": len(desktops)}

    # ---------- 录音忙闲 ----------

    def set_busy(self, room_id: str, busy: bool) -> None:
        self._busy[room_id] = busy

    def is_busy(self, room_id: str) -> bool:
        return bool(self._busy.get(room_id))

    # ---------- 下发 ----------

    async def send_to(self, connection: VoiceConnection, payload: dict[str, Any]) -> bool:
        payload.setdefault("ts", _now_ms())
        return await connection.send_json(payload)

    async def broadcast(
        self,
        room_id: str,
        payload: dict[str, Any],
        *,
        role: str | None = None,
        exclude: VoiceConnection | None = None,
    ) -> int:
        payload.setdefault("ts", _now_ms())
        sent = 0
        for item in list(self._rooms.get(room_id, ())):
            if item is exclude or not item.connected:
                continue
            if role is not None and item.role != role:
                continue
            if await item.send_json(payload):
                sent += 1
        return sent

    async def broadcast_to_phone(self, room_id: str, payload: dict[str, Any]) -> int:
        return await self.broadcast(room_id, payload, role=ROLE_PHONE)

    async def broadcast_snapshot(
        self,
        room_id: str,
        *,
        seg_uid: str,
        seq: int,
        rev: int,
        text: str,
        final: bool,
    ) -> int:
        """桌面端唯一的下发文本消息：永远是整段快照，不是 diff。"""
        payload = {
            "type": "segment.snapshot",
            "segId": seg_uid,
            "seq": seq,
            "rev": rev,
            "text": text,
            "final": final,
            "roomBusy": self.is_busy(room_id),
        }
        return await self.broadcast(room_id, payload, role=ROLE_DESKTOP)

    async def broadcast_room_state(self, room_id: str) -> None:
        await self.broadcast(room_id, {"type": "room.state", **self.room_state(room_id)})

    async def close_room(self, room_id: str, code: int = 1008, reason: str = "房间已关闭") -> None:
        for item in list(self._rooms.get(room_id, ())):
            item.connected = False
            with contextlib.suppress(Exception):
                await item.websocket.close(code=code, reason=reason)
        self._rooms.pop(room_id, None)
        self._busy.pop(room_id, None)


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


voice_hub = VoiceHub()

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket
from fastapi.responses import JSONResponse, StreamingResponse

HOP_BY_HOP_HEADERS = frozenset(
    {
        "authorization",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "x-api-key",
        "x-local-agent-token",
    }
)


def filter_headers(headers: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() not in HOP_BY_HOP_HEADERS}


def encode_body_frame(request_id: str, body: bytes) -> bytes:
    request_id_bytes = request_id.encode("utf-8")
    if len(request_id_bytes) > 65535:
        raise ValueError("requestId 过长")
    return len(request_id_bytes).to_bytes(2, "big") + request_id_bytes + body


def decode_body_frame(frame: bytes) -> tuple[str, bytes]:
    if len(frame) < 2:
        raise ValueError("二进制帧长度不足")
    request_id_length = int.from_bytes(frame[:2], "big")
    if len(frame) < 2 + request_id_length:
        raise ValueError("二进制帧 requestId 不完整")
    return frame[2 : 2 + request_id_length].decode("utf-8"), frame[2 + request_id_length :]


@dataclass
class PendingRequest:
    events: asyncio.Queue[tuple[str, Any]] = field(default_factory=asyncio.Queue)


@dataclass
class AgentConnection:
    agent_id: str
    websocket: WebSocket
    routes: dict[str, dict[str, Any]]
    pending: dict[str, PendingRequest] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send_json(self, payload: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.websocket.send_json(payload)

    async def send_bytes(self, payload: bytes) -> None:
        async with self.send_lock:
            await self.websocket.send_bytes(payload)


class LocalAgentRelay:
    def __init__(self) -> None:
        self._routes: dict[str, AgentConnection] = {}

    def register(self, connection: AgentConnection) -> None:
        for route_id in connection.routes:
            self._routes[route_id] = connection

    def online_agents(self) -> list[dict[str, Any]]:
        connections = {id(connection): connection for connection in self._routes.values()}.values()
        return [
            {
                "agent_id": connection.agent_id,
                "routes": [
                    connection.routes[route_id]
                    if isinstance(connection.routes, dict)
                    else {"id": route_id, "name": route_id, "provider": "openai_generic"}
                    for route_id in sorted(connection.routes)
                ],
            }
            for connection in sorted(connections, key=lambda item: item.agent_id)
        ]

    def is_agent_online(self, agent_id: str) -> bool:
        return any(connection.agent_id == agent_id for connection in self._routes.values())

    def is_agent_online_for_route(self, route_id: str) -> bool:
        return route_id in self._routes

    async def disconnect(self, connection: AgentConnection, message: str = "Agent 连接已断开") -> None:
        for route_id in connection.routes:
            if self._routes.get(route_id) is connection:
                del self._routes[route_id]
        for pending in connection.pending.values():
            await pending.events.put(("error", message))
        connection.pending.clear()

    async def receive_json(self, connection: AgentConnection, frame: dict[str, Any]) -> None:
        if frame.get("type") == "ping":
            await connection.send_json({"type": "pong"})
            return
        request_id = frame.get("requestId")
        if not isinstance(request_id, str) or request_id not in connection.pending:
            return
        if frame.get("type") == "response-start":
            await connection.pending[request_id].events.put(
                ("response-start", (int(frame.get("statusCode") or 502), filter_headers(frame.get("headers") or {})))
            )
        elif frame.get("type") == "response-end":
            await connection.pending[request_id].events.put(("response-end", None))
        elif frame.get("type") == "error":
            await connection.pending[request_id].events.put(("error", str(frame.get("message") or "Agent 转发失败")))

    async def receive_bytes(self, connection: AgentConnection, frame: bytes) -> None:
        request_id, body = decode_body_frame(frame)
        pending = connection.pending.get(request_id)
        if pending is not None:
            await pending.events.put(("response-body", body))

    async def forward(
        self,
        route_id: str,
        method: str,
        path: str,
        headers: dict[str, str],
        body: AsyncIterator[bytes],
    ) -> JSONResponse | StreamingResponse:
        connection = self._routes.get(route_id)
        if connection is None:
            return JSONResponse(status_code=503, content={"error": {"message": "目标 route 没有在线 Agent"}})
        request_id = str(uuid.uuid4())
        pending = PendingRequest()
        connection.pending[request_id] = pending
        response_started = False
        try:
            await connection.send_json(
                {
                    "type": "request",
                    "requestId": request_id,
                    "routeId": route_id,
                    "method": method,
                    "path": path,
                    "headers": filter_headers(headers),
                }
            )
            async for chunk in body:
                if chunk:
                    await connection.send_bytes(encode_body_frame(request_id, chunk))
            await connection.send_json({"type": "request-end", "requestId": request_id})
            event_type, payload = await asyncio.wait_for(pending.events.get(), timeout=120)
            if event_type == "error":
                return JSONResponse(status_code=502, content={"error": {"message": payload}})
            if event_type != "response-start":
                return JSONResponse(status_code=502, content={"error": {"message": "Agent 未返回响应起始帧"}})
            status_code, response_headers = payload
            response_started = True
            return StreamingResponse(self._stream_response(connection, request_id, pending), status_code=status_code, headers=response_headers)
        except (asyncio.TimeoutError, RuntimeError) as error:
            return JSONResponse(status_code=504, content={"error": {"message": f"Agent 响应超时: {error}"}})
        finally:
            if not response_started:
                connection.pending.pop(request_id, None)

    async def _stream_response(
        self, connection: AgentConnection, request_id: str, pending: PendingRequest
    ) -> AsyncIterator[bytes]:
        try:
            while True:
                event_type, payload = await asyncio.wait_for(pending.events.get(), timeout=120)
                if event_type == "response-body":
                    yield payload
                elif event_type == "response-end":
                    return
                elif event_type == "error":
                    return
        finally:
            connection.pending.pop(request_id, None)
            try:
                await connection.send_json({"type": "cancel", "requestId": request_id})
            except RuntimeError:
                pass


local_agent_relay = LocalAgentRelay()


def parse_agent_registration(text: str, expected_token: str) -> tuple[str, dict[str, dict[str, Any]]] | None:
    try:
        frame = json.loads(text)
    except json.JSONDecodeError:
        return None
    route_items = frame.get("routes")
    if (
        not expected_token
        or frame.get("type") != "register"
        or frame.get("token") != expected_token
        or not isinstance(frame.get("agentId"), str)
        or not frame["agentId"]
        or not isinstance(route_items, list)
        or not route_items
    ):
        return None
    routes: dict[str, dict[str, Any]] = {}
    for item in route_items:
        if isinstance(item, str) and item:
            routes[item] = {"id": item, "name": item, "provider": "openai_generic"}
            continue
        if not isinstance(item, dict):
            return None
        route_id = item.get("id")
        provider = item.get("provider", "openai_generic")
        if (
            not isinstance(route_id, str)
            or not route_id
            or route_id in routes
            or not isinstance(provider, str)
        ):
            return None
        name = item.get("name")
        routes[route_id] = {
            "id": route_id,
            "name": name if isinstance(name, str) and name else route_id,
            "provider": provider,
        }
    return frame["agentId"], routes
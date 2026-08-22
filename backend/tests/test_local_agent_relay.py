from __future__ import annotations

import asyncio

import pytest

from app.services.local_agent_relay import (
    AgentConnection,
    LocalAgentRelay,
    decode_body_frame,
    encode_body_frame,
    parse_agent_registration,
)


class FakeWebSocket:
    def __init__(self) -> None:
        self.json_frames: list[dict] = []
        self.byte_frames: list[bytes] = []

    async def send_json(self, payload: dict) -> None:
        self.json_frames.append(payload)

    async def send_bytes(self, payload: bytes) -> None:
        self.byte_frames.append(payload)


def test_body_frame_keeps_raw_bytes() -> None:
    payload = b"data: first\n\ndata: second\n\n"
    request_id, decoded = decode_body_frame(encode_body_frame("request-1", payload))
    assert request_id == "request-1"
    assert decoded == payload


def test_registration_requires_valid_shared_token() -> None:
    assert parse_agent_registration('{"type":"register","agentId":"home","token":"secret","routes":["deepseek"]}', "secret") == (
        "home",
        {"deepseek": {"id": "deepseek", "name": "deepseek", "provider": "openai_generic"}},
    )
    assert parse_agent_registration('{"type":"register","agentId":"home","token":"wrong","routes":["deepseek"]}', "secret") is None


def test_registration_ignores_legacy_declared_models() -> None:
    registration = parse_agent_registration(
        '{"type":"register","agentId":"home","token":"secret","routes":[{"id":"deepseek","models":["untrusted-model"]}]}',
        "secret",
    )
    assert registration == (
        "home",
        {"deepseek": {"id": "deepseek", "name": "deepseek", "provider": "openai_generic"}},
    )


@pytest.mark.asyncio
async def test_forward_streams_agent_response_without_losing_chunks() -> None:
    relay = LocalAgentRelay()
    websocket = FakeWebSocket()
    connection = AgentConnection(agent_id="home", websocket=websocket, routes={"deepseek": {"id": "deepseek", "name": "deepseek", "provider": "openai_generic"}})
    relay.register(connection)

    async def body():
        yield b'{"model":"x"}'

    task = asyncio.create_task(relay.forward("deepseek", "POST", "/v1/messages", {}, body()))
    await asyncio.sleep(0)
    request = websocket.json_frames[0]
    request_id = request["requestId"]
    assert request["path"] == "/v1/messages"
    assert decode_body_frame(websocket.byte_frames[0]) == (request_id, b'{"model":"x"}')

    await relay.receive_json(connection, {"type": "response-start", "requestId": request_id, "statusCode": 200, "headers": {"content-type": "text/event-stream"}})
    response = await task
    await relay.receive_bytes(connection, encode_body_frame(request_id, b"data: one\n\n"))
    await relay.receive_bytes(connection, encode_body_frame(request_id, b"data: two\n\n"))
    await relay.receive_json(connection, {"type": "response-end", "requestId": request_id})

    assert response.status_code == 200
    assert b"".join([chunk async for chunk in response.body_iterator]) == b"data: one\n\ndata: two\n\n"


@pytest.mark.asyncio
async def test_forward_does_not_send_gateway_credentials_to_agent() -> None:
    relay = LocalAgentRelay()
    websocket = FakeWebSocket()
    connection = AgentConnection(
        agent_id="home",
        websocket=websocket,
        routes={"deepseek": {"id": "deepseek", "name": "deepseek", "provider": "openai_generic"}},
    )
    relay.register(connection)

    async def body():
        if False:
            yield b""

    task = asyncio.create_task(
        relay.forward(
            "deepseek",
            "POST",
            "/v1/messages",
            {
                "Authorization": "Bearer gateway-key",
                "X-API-Key": "gateway-key",
                "X-Local-Agent-Token": "internal-token",
                "X-Trace": "request-1",
            },
            body(),
        )
    )
    await asyncio.sleep(0)
    request = websocket.json_frames[0]
    request_id = request["requestId"]
    assert request["headers"] == {"X-Trace": "request-1"}

    await relay.receive_json(connection, {"type": "response-start", "requestId": request_id, "statusCode": 204})
    response = await task
    await relay.receive_json(connection, {"type": "response-end", "requestId": request_id})
    assert response.status_code == 204
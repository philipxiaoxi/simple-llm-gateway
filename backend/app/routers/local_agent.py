from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select

from app.clock import utcnow
from app.config import get_settings
from app.db import get_session_factory
from app.deps import get_current_admin
from app.models import GatewayAgent, GatewayAgentRoute, UpstreamAccount
from app.providers import get_provider
from app.services.local_agent_relay import (
    AgentConnection,
    local_agent_relay,
    parse_agent_registration,
)

router = APIRouter(tags=["local-agent"])
LOCAL_RELAY_BASE_URL = "http://127.0.0.1:8000"


@router.get("/api/admin/agents", dependencies=[Depends(get_current_admin)])
def list_online_agents() -> dict[str, object]:
    session = get_session_factory()()
    try:
        agents = session.scalars(select(GatewayAgent).order_by(GatewayAgent.agent_id)).all()
        return {"items": [_agent_to_out(agent) for agent in agents], "total": len(agents)}
    finally:
        session.close()


@router.get("/api/admin/agents/{agent_id}", dependencies=[Depends(get_current_admin)], response_model=None)
def get_agent(agent_id: str) -> dict[str, object] | JSONResponse:
    session = get_session_factory()()
    try:
        agent = session.scalar(select(GatewayAgent).where(GatewayAgent.agent_id == agent_id))
        if agent is None:
            return JSONResponse(status_code=404, content={"detail": "网关代理不存在"})
        return _agent_to_out(agent)
    finally:
        session.close()


@router.post("/api/admin/agents/{agent_id}/routes/{route_id}/models", dependencies=[Depends(get_current_admin)])
async def refresh_agent_route_models(agent_id: str, route_id: str) -> JSONResponse:
    session = get_session_factory()()
    try:
        route = session.scalar(
            select(GatewayAgentRoute)
            .join(GatewayAgent)
            .where(GatewayAgent.agent_id == agent_id, GatewayAgentRoute.route_id == route_id)
        )
        account = session.scalar(select(UpstreamAccount).where(UpstreamAccount.agent_route_id == route_id))
        if route is None or account is None:
            return JSONResponse(status_code=404, content={"detail": "网关代理路由不存在"})
        if not local_agent_relay.is_agent_online(agent_id):
            return JSONResponse(status_code=503, content={"detail": "网关代理当前离线，无法刷新模型"})
        from app.services.probe import list_account_models

        result = await list_account_models(account)
        if result["ok"]:
            route.models_json = account.models_json
            route.models_updated_at = account.models_updated_at
        session.commit()
        return JSONResponse(content=result)
    finally:
        session.close()


@router.websocket("/agent/connect")
async def connect_agent(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        registration = parse_agent_registration(await websocket.receive_text(), get_settings().local_agent_token)
        if registration is None:
            await websocket.close(code=1008, reason="Agent 注册无效")
            return
        agent_id, routes = registration
        connection = AgentConnection(agent_id=agent_id, websocket=websocket, routes=routes)
        local_agent_relay.register(connection)
        _sync_agent(agent_id, routes)
        await connection.send_json({"type": "registered", "agentId": agent_id})
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message["type"] == "websocket.receive":
                if message.get("text") is not None:
                    await local_agent_relay.receive_json(connection, json.loads(message["text"]))
                elif message.get("bytes") is not None:
                    await local_agent_relay.receive_bytes(connection, message["bytes"])
    except WebSocketDisconnect:
        pass
    finally:
        if "connection" in locals():
            await local_agent_relay.disconnect(connection)
            _mark_agent_offline(connection.agent_id)


@router.api_route("/r/{route_id}/{upstream_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"], response_model=None)
async def forward_to_local_agent(
    route_id: str,
    upstream_path: str,
    request: Request,
    relay_token: str | None = Header(default=None, alias="x-local-agent-token"),
) -> JSONResponse | StreamingResponse:
    settings = get_settings()
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        return JSONResponse(status_code=403, content={"error": {"message": "Relay 仅允许 Backend 本机访问"}})
    if not settings.local_agent_token or relay_token is None or not secrets.compare_digest(relay_token, settings.local_agent_token):
        return JSONResponse(status_code=401, content={"error": {"message": "Relay 内部认证失败"}})
    path = "/" + upstream_path
    if request.url.query:
        path += "?" + request.url.query
    return await local_agent_relay.forward(route_id, request.method, path, dict(request.headers), request.stream())


def _sync_agent(agent_id: str, routes: dict[str, dict[str, object]]) -> None:
    session = get_session_factory()()
    try:
        agent = session.scalar(select(GatewayAgent).where(GatewayAgent.agent_id == agent_id))
        now = utcnow()
        if agent is None:
            agent = GatewayAgent(agent_id=agent_id, status="online", last_connected_at=now, updated_at=now)
            session.add(agent)
            session.flush()
        else:
            agent.status = "online"
            agent.last_connected_at = now
            agent.updated_at = now
        registered_route_ids = set(routes)
        for stored_route in agent.routes:
            if stored_route.route_id in registered_route_ids:
                continue
            account = session.scalar(select(UpstreamAccount).where(UpstreamAccount.agent_route_id == stored_route.route_id))
            if account is not None and account.source == "agent":
                session.delete(account)
            session.delete(stored_route)
        for route_id, route in routes.items():
            provider_id = str(route["provider"])
            try:
                provider = get_provider(provider_id)
            except ValueError:
                continue
            stored_route = session.scalar(select(GatewayAgentRoute).where(GatewayAgentRoute.route_id == route_id))
            account = session.scalar(select(UpstreamAccount).where(UpstreamAccount.agent_route_id == route_id))
            if account is None:
                account = UpstreamAccount(
                    name=str(route["name"]),
                    provider=provider_id,
                    source="agent",
                    agent_route_id=route_id,
                    auth_type=provider.auth_type,
                        base_url=f"{LOCAL_RELAY_BASE_URL}/r/{route_id}/v1",
                    status="active",
                    risk_level="medium",
                )
                session.add(account)
            else:
                account.name = str(route["name"])
                account.provider = provider_id
                account.auth_type = provider.auth_type
                account.base_url = f"{LOCAL_RELAY_BASE_URL}/r/{route_id}/v1"
            if stored_route is None:
                session.add(
                    GatewayAgentRoute(
                        agent_id=agent.id,
                        route_id=route_id,
                        name=str(route["name"]),
                        provider=provider_id,
                    )
                )
            else:
                stored_route.agent_id = agent.id
                stored_route.name = str(route["name"])
                stored_route.provider = provider_id
                stored_route.updated_at = now
        session.commit()
    finally:
        session.close()


def _mark_agent_offline(agent_id: str) -> None:
    if local_agent_relay.is_agent_online(agent_id):
        return
    session = get_session_factory()()
    try:
        agent = session.scalar(select(GatewayAgent).where(GatewayAgent.agent_id == agent_id))
        if agent is not None:
            now = utcnow()
            agent.status = "offline"
            agent.last_disconnected_at = now
            agent.updated_at = now
            session.commit()
    finally:
        session.close()


def _agent_to_out(agent: GatewayAgent) -> dict[str, object]:
    return {
        "agent_id": agent.agent_id,
        "status": "online" if local_agent_relay.is_agent_online(agent.agent_id) else "offline",
        "last_connected_at": agent.last_connected_at.isoformat() if agent.last_connected_at else None,
        "last_disconnected_at": agent.last_disconnected_at.isoformat() if agent.last_disconnected_at else None,
        "routes": [
            {
                "id": route.route_id,
                "name": route.name,
                "provider": route.provider,
                "models": json.loads(route.models_json) if route.models_json else [],
                "models_updated_at": route.models_updated_at.isoformat() if route.models_updated_at else None,
            }
            for route in agent.routes
        ],
    }
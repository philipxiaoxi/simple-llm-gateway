from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_session_factory
from app.models import UpstreamAccount
from app.routers.local_agent import _mark_agent_offline, _sync_agent
from app.services.local_agent_relay import AgentConnection, local_agent_relay


class FakeWebSocket:
    async def send_json(self, payload: object) -> None:
        pass

    async def send_bytes(self, payload: bytes) -> None:
        pass


def test_list_online_agents_requires_admin(client: TestClient) -> None:
    assert client.get("/api/admin/agents").status_code == 401


def test_relay_requires_internal_token(client: TestClient) -> None:
    response = client.post("/r/unknown/v1/chat/completions", json={})
    assert response.status_code == 401
    assert response.json() == {"error": {"message": "Relay 内部认证失败"}}

    authenticated = client.post(
        "/r/unknown/v1/chat/completions",
        headers={"X-Local-Agent-Token": "unit-test-local-agent-token"},
        json={},
    )
    assert authenticated.status_code == 503


def test_agents_keep_registered_machine_and_routes_after_disconnect(client: TestClient, auth_headers: dict[str, str]) -> None:
    routes = {
        "deepseek-local": {"id": "deepseek-local", "name": "DeepSeek", "provider": "deepseek"},
        "vendor-a": {"id": "vendor-a", "name": "Vendor A", "provider": "openai_generic"},
    }
    _sync_agent("macbook-studio", routes)
    connection = AgentConnection(agent_id="macbook-studio", websocket=FakeWebSocket(), routes=routes)  # type: ignore[arg-type]
    local_agent_relay.register(connection)
    try:
        response = client.get("/api/admin/agents", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == {
            "items": [{
                "agent_id": "macbook-studio",
                "status": "online",
                "last_connected_at": response.json()["items"][0]["last_connected_at"],
                "last_disconnected_at": None,
                "routes": [
                    {"id": "deepseek-local", "name": "DeepSeek", "provider": "deepseek", "models": [], "models_updated_at": None},
                    {"id": "vendor-a", "name": "Vendor A", "provider": "openai_generic", "models": [], "models_updated_at": None},
                ],
            }],
            "total": 1,
        }
    finally:
        local_agent_relay._routes.clear()
    _mark_agent_offline("macbook-studio")
    detail = client.get("/api/admin/agents/macbook-studio", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "offline"
    assert [route["id"] for route in detail.json()["routes"]] == ["deepseek-local", "vendor-a"]


def test_agent_routes_use_local_relay_base_url(client: TestClient) -> None:
    _sync_agent("macbook-studio", {"deepseek-local": {"id": "deepseek-local", "name": "DeepSeek", "provider": "deepseek"}})
    session = get_session_factory()()
    try:
        account = session.scalar(select(UpstreamAccount).where(UpstreamAccount.agent_route_id == "deepseek-local"))
        assert account is not None
        assert account.base_url == "http://127.0.0.1:8000/r/deepseek-local/v1"
    finally:
        session.close()


def test_refresh_agent_route_models_rejects_offline_agent(client: TestClient, auth_headers: dict[str, str]) -> None:
    _sync_agent("macbook-studio", {"deepseek-local": {"id": "deepseek-local", "name": "DeepSeek", "provider": "deepseek"}})
    response = client.post("/api/admin/agents/macbook-studio/routes/deepseek-local/models", headers=auth_headers)
    assert response.status_code == 503
    assert response.json() == {"detail": "Agent 当前离线，无法刷新模型"}


def test_refresh_agent_route_models_syncs_upstream_result(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    routes = {"deepseek-local": {"id": "deepseek-local", "name": "DeepSeek", "provider": "deepseek"}}
    _sync_agent("macbook-studio", routes)
    connection = AgentConnection(agent_id="macbook-studio", websocket=FakeWebSocket(), routes=routes)  # type: ignore[arg-type]
    local_agent_relay.register(connection)

    async def fake_list_account_models(account) -> dict[str, object]:
        account.models_json = '["deepseek-chat", "deepseek-reasoner"]'
        from app.clock import utcnow

        account.models_updated_at = utcnow()
        return {"ok": True, "models": ["deepseek-chat", "deepseek-reasoner"], "source": "upstream"}

    monkeypatch.setattr("app.services.probe.list_account_models", fake_list_account_models)
    try:
        response = client.post("/api/admin/agents/macbook-studio/routes/deepseek-local/models", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == {"ok": True, "models": ["deepseek-chat", "deepseek-reasoner"], "source": "upstream"}
        detail = client.get("/api/admin/agents/macbook-studio", headers=auth_headers)
        assert detail.json()["routes"][0]["models"] == ["deepseek-chat", "deepseek-reasoner"]
    finally:
        local_agent_relay._routes.clear()
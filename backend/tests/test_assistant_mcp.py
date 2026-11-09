from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi.testclient import TestClient


def _parse_sse(raw: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in raw.split("\n\n"):
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if event and data:
            events.append((event, json.loads(data)))
    return events


def _run_tool(
    permissions: set[str],
    name: str,
    arguments: dict[str, Any],
    *,
    conversation_id: str = "test-conversation",
    public_origin: str = "",
) -> dict[str, Any]:
    from app.assistant.tools import execute_tool
    from app.main import app

    return asyncio.run(
        execute_tool(
            app=app,
            token="unused",
            permissions=permissions,
            admin_id=1,
            conversation_id=conversation_id,
            tool_call_id="test-call",
            name=name,
            arguments=arguments,
            public_origin=public_origin,
        )
    )


def test_rewrite_public_links(client: TestClient) -> None:
    from app.assistant.links import public_origin_from_request, rewrite_public_links
    from app.config import get_settings

    base = get_settings().app_base_url.rstrip("/")
    payload = {"items": [{"thumb_url": f"{base}/api/x?token=1", "other": 1}]}
    rewritten = rewrite_public_links(payload, "https://preview.example.com")
    assert rewritten["items"][0]["thumb_url"] == "https://preview.example.com/api/x?token=1"
    assert rewritten["items"][0]["other"] == 1

    class _Request:
        headers = {"origin": "https://preview.example.com", "referer": "https://preview.example.com/"}

    assert public_origin_from_request(_Request()) == "https://preview.example.com"


def test_mcp_search_filters_by_permission() -> None:
    result = _run_tool({"mcp_knowledge:view"}, "mcp_search", {"query": "知识库"})
    assert result["ok"] is True, result
    items = result["result"]["items"]
    assert items
    assert {item["capability"] for item in items} == {"knowledge"}


def test_mcp_call_works_without_mcp_key(client: TestClient, auth_headers: dict[str, str]) -> None:
    result = _run_tool(
        {"generation:settings"},
        "mcp_call",
        {"tool_name": "image_models", "arguments": {"available_only": False}},
    )
    assert result["ok"] is True, result
    assert result["result"]["kind"] == "image"
    assert result["result"]["models"]

    # 助手隐藏 Key 不出现在 MCP Key 列表里
    keys = client.get("/api/admin/mcp/keys", headers=auth_headers).json()
    assert all(item["name"] != "内置 AI 助手" for item in keys)


def test_mcp_call_rewrites_internal_origin(client: TestClient, monkeypatch) -> None:
    from app.assistant import tools as tools_module
    from app.config import get_settings

    base = get_settings().app_base_url.rstrip("/")

    async def fake_invoke(**_kwargs: Any) -> dict[str, Any]:
        return {"items": [{"thumb_url": f"{base}/api/x.png?token=1"}]}

    monkeypatch.setattr(tools_module, "_invoke_mcp_tool", fake_invoke)
    result = _run_tool(
        {"*"},
        "mcp_call",
        {"tool_name": "portrait_search", "arguments": {"query": "x"}},
        public_origin="https://preview.example.com",
    )
    assert result["ok"] is True, result
    assert result["result"]["items"][0]["thumb_url"] == "https://preview.example.com/api/x.png?token=1"


def test_mcp_call_rejects_unauthorized_capability() -> None:
    result = _run_tool({"logs:view"}, "mcp_call", {"tool_name": "image_models", "arguments": {}})
    assert result["ok"] is False
    assert "没有可用的 MCP 工具" in result["error"]


def test_hidden_assistant_key_is_not_exposed(client: TestClient, auth_headers: dict[str, str]) -> None:
    from app.assistant.mcp_bridge import get_assistant_key
    from app.db import get_session_factory

    session = get_session_factory()()
    try:
        key = get_assistant_key(session, 1, {"*"})
        session.commit()
        key_id = key.id
    finally:
        session.close()

    keys = client.get("/api/admin/mcp/keys", headers=auth_headers).json()
    assert all(item["id"] != key_id for item in keys)
    assert client.get(f"/api/admin/mcp/keys/{key_id}/reveal", headers=auth_headers).status_code == 404
    assert client.delete(f"/api/admin/mcp/keys/{key_id}", headers=auth_headers).status_code == 404


def test_mcp_destructive_call_requires_approval(client: TestClient, auth_headers: dict[str, str]) -> None:
    conversation = client.post("/api/admin/assistant/conversations", headers=auth_headers, json={})
    assert conversation.status_code == 201, conversation.text
    result = _run_tool(
        {"*"},
        "mcp_call",
        {"tool_name": "site_delete", "arguments": {"site_id": "not-exist"}, "reason": "测试删除"},
        conversation_id=conversation.json()["id"],
    )
    assert result["kind"] == "approval", result
    approval = result["approval"]
    assert approval["status"] == "pending"
    assert approval["method"] == "MCP"
    assert approval["api_id"] == "MCP site_delete"
    assert approval["body"] == {"site_id": "not-exist"}


def test_mcp_approval_executes_after_confirm(client: TestClient, auth_headers: dict[str, str]) -> None:
    conversation = client.post("/api/admin/assistant/conversations", headers=auth_headers, json={})
    assert conversation.status_code == 201, conversation.text
    conversation_id = conversation.json()["id"]
    result = _run_tool(
        {"*"},
        "mcp_call",
        {"tool_name": "site_delete", "arguments": {"slug": "not-exist"}, "reason": "测试删除"},
        conversation_id=conversation_id,
    )
    approval_id = result["approval"]["id"]

    with client.stream(
        "POST",
        f"/api/admin/assistant/approvals/{approval_id}/decision",
        headers=auth_headers,
        json={"decision": "approve"},
    ) as response:
        assert response.status_code == 200, response.text
        events = _parse_sse("".join(response.iter_text()))
    tool_result = next(data for name, data in events if name == "tool_result")
    assert tool_result["name"] == "mcp_call"
    assert tool_result["ok"] is False
    assert "站点不存在" in tool_result["error"]

    detail = client.get(f"/api/admin/assistant/approvals/{approval_id}", headers=auth_headers).json()
    assert detail["status"] == "failed"


def test_assistant_key_whitelist_follows_permissions(client: TestClient, auth_headers: dict[str, str]) -> None:
    from app.assistant.mcp_bridge import get_assistant_key
    from app.db import get_session_factory

    session = get_session_factory()()
    try:
        key = get_assistant_key(session, 1, {"mcp_knowledge:view"})
        session.commit()
        assert {item.capability_id for item in key.capabilities} == {"knowledge"}

        wide = get_assistant_key(session, 1, {"*"})
        session.commit()
        assert wide.id == key.id
        assert {"knowledge", "image_gen", "video_gen"} <= {item.capability_id for item in wide.capabilities}
    finally:
        session.close()

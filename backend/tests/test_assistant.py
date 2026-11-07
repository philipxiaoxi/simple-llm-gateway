from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient


class FakeStream:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks
        self._index = 0

    def __aiter__(self) -> FakeStream:
        return self

    async def __anext__(self) -> dict[str, Any]:
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


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


def _tool_call_chunk(call_id: str, name: str, arguments: str, index: int = 0) -> dict[str, Any]:
    return {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": call_id,
                            "function": {"name": name, "arguments": arguments},
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


def _text_chunks(text: str) -> list[dict[str, Any]]:
    return [
        {"choices": [{"delta": {"content": text}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
    ]


def _create_account(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/admin/accounts",
        headers=auth,
        json={
            "name": "助手测试账号",
            "provider": "openai_generic",
            "api_key": "sk-assistant-test",
            "base_url": "https://example.com/v1",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _configure(client: TestClient, auth: dict[str, str], account_id: int, **overrides: Any) -> dict[str, Any]:
    payload = {
        "enabled": True,
        "account_id": account_id,
        "model": "gpt-test",
        "system_prompt": "你是测试助手。",
        **overrides,
    }
    response = client.put("/api/admin/assistant/settings", headers=auth, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _create_conversation(client: TestClient, auth: dict[str, str]) -> str:
    response = client.post("/api/admin/assistant/conversations", headers=auth, json={})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _stream_message(
    client: TestClient,
    auth: dict[str, str],
    conversation_id: str,
    content: str,
    page: dict[str, Any] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    with client.stream(
        "POST",
        f"/api/admin/assistant/conversations/{conversation_id}/messages",
        headers=auth,
        json={"content": content, "page": page or {"path": "/"}},
    ) as response:
        assert response.status_code == 200, response.text
        raw = "".join(response.iter_text())
    return _parse_sse(raw)


def _stream_decision(client: TestClient, auth: dict[str, str], approval_id: str, decision: str) -> list[tuple[str, dict[str, Any]]]:
    with client.stream(
        "POST",
        f"/api/admin/assistant/approvals/{approval_id}/decision",
        headers=auth,
        json={"decision": decision, "page": {"path": "/roles"}},
    ) as response:
        assert response.status_code == 200, response.text
        raw = "".join(response.iter_text())
    return _parse_sse(raw)


def test_assistant_settings(client: TestClient, auth_headers: dict[str, str]) -> None:
    account = _create_account(client, auth_headers)
    payload = _configure(client, auth_headers, account["id"])
    assert payload["configured"] is True
    assert payload["account_name"] == "助手测试账号"
    tools = client.get("/api/admin/assistant/tools", headers=auth_headers).json()
    names = {item["name"] for item in tools["items"]}
    assert {"api_search", "api_describe", "api_call", "ui_navigate"} <= names


def test_assistant_catalog_filters_permissions(client: TestClient, auth_headers: dict[str, str]) -> None:
    from app.assistant.api_catalog import build_catalog, search_operations
    from app.main import app

    operations = build_catalog(app)
    ids = {item.id for item in operations}
    assert "GET /api/admin/mcp/douyin/jobs" in ids
    assert "POST /api/admin/mcp/douyin/jobs" in ids
    assert "GET /api/admin/keys/{key_id}/reveal" not in ids
    assert "GET /api/admin/mcp/sites/{site_id}/token" not in ids

    import asyncio

    from app.assistant.tools import execute_tool

    result = asyncio.run(
        execute_tool(
            app=app,
            token="unused",
            permissions={"logs:view"},
            admin_id=1,
            conversation_id="test",
            tool_call_id="call",
            name="api_search",
            arguments={"query": "创建上游账号"},
        )
    )
    assert result["ok"] is True
    assert all(item["id"] != "POST /api/admin/accounts" for item in result["result"]["items"])

    read_only = search_operations(operations, "抖音", read_only=True, limit=10)
    assert read_only and all(item.read_only for item in read_only)


def test_assistant_read_only_api_call(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    account = _create_account(client, auth_headers)
    _configure(client, auth_headers, account["id"])
    conversation_id = _create_conversation(client, auth_headers)

    from app.assistant import agent

    calls = {"count": 0}

    async def fake_call_chat(account, messages, model, stream, extra, api_key):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeStream(
                [_tool_call_chunk("call_roles", "api_call", '{"api_id":"GET /api/admin/roles"}')]
            )
        return FakeStream(_text_chunks("角色列表已读取。"))

    monkeypatch.setattr(agent, "call_chat", fake_call_chat)
    events = _stream_message(client, auth_headers, conversation_id, "列出角色")
    results = [data for name, data in events if name == "tool_result"]
    assert results and results[0]["ok"] is True
    assert any(item["name"] == "管理员" for item in results[0]["result"])


def test_assistant_write_requires_approval_and_approve(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    account = _create_account(client, auth_headers)
    _configure(client, auth_headers, account["id"])
    conversation_id = _create_conversation(client, auth_headers)

    from app.assistant import agent

    calls = {"count": 0}

    async def fake_call_chat(account, messages, model, stream, extra, api_key):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeStream(
                [
                    _tool_call_chunk(
                        "call_create_role",
                        "api_call",
                        '{"api_id":"POST /api/admin/roles","body":{"name":"审批测试角色","permissions":["logs:view"]},"reason":"用户要求创建角色"}',
                    )
                ]
            )
        return FakeStream(_text_chunks("角色已创建。"))

    monkeypatch.setattr(agent, "call_chat", fake_call_chat)
    events = _stream_message(client, auth_headers, conversation_id, "创建角色")
    names = [name for name, _ in events]
    assert "approval_required" in names
    done = next(data for name, data in events if name == "done")
    assert done["awaiting_approval"] is True
    approval = next(data["approval"] for name, data in events if name == "approval_required")
    assert approval["method"] == "POST"

    roles_before = client.get("/api/admin/roles", headers=auth_headers).json()
    assert not any(role["name"] == "审批测试角色" for role in roles_before)

    blocked = client.post(
        f"/api/admin/assistant/conversations/{conversation_id}/messages",
        headers=auth_headers,
        json={"content": "继续"},
    )
    assert blocked.status_code == 409

    decision_events = _stream_decision(client, auth_headers, approval["id"], "approve")
    decision_names = [name for name, _ in decision_events]
    assert "tool_result" in decision_names
    result = next(data for name, data in decision_events if name == "tool_result")
    assert result["ok"] is True

    roles = client.get("/api/admin/roles", headers=auth_headers).json()
    created = next(role for role in roles if role["name"] == "审批测试角色")
    client.delete(f"/api/admin/roles/{created['id']}", headers=auth_headers)

    detail = client.get(
        f"/api/admin/assistant/conversations/{conversation_id}", headers=auth_headers
    ).json()
    assert any(call.get("approval", {}).get("status") == "executed" for message in detail["messages"] for call in message["tool_calls"])


def test_assistant_reject_write(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    account = _create_account(client, auth_headers)
    _configure(client, auth_headers, account["id"])
    conversation_id = _create_conversation(client, auth_headers)

    from app.assistant import agent

    calls = {"count": 0}

    async def fake_call_chat(account, messages, model, stream, extra, api_key):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeStream(
                [
                    _tool_call_chunk(
                        "call_reject_role",
                        "api_call",
                        '{"api_id":"POST /api/admin/roles","body":{"name":"拒绝测试角色","permissions":["logs:view"]}}',
                    )
                ]
            )
        return FakeStream(_text_chunks("已取消创建。"))

    monkeypatch.setattr(agent, "call_chat", fake_call_chat)
    events = _stream_message(client, auth_headers, conversation_id, "创建角色")
    approval = next(data["approval"] for name, data in events if name == "approval_required")
    decision_events = _stream_decision(client, auth_headers, approval["id"], "reject")
    result = next(data for name, data in decision_events if name == "tool_result")
    assert result["ok"] is False
    assert "拒绝" in result["error"]
    roles = client.get("/api/admin/roles", headers=auth_headers).json()
    assert not any(role["name"] == "拒绝测试角色" for role in roles)


def test_assistant_ui_navigate(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    account = _create_account(client, auth_headers)
    _configure(client, auth_headers, account["id"])
    conversation_id = _create_conversation(client, auth_headers)

    from app.assistant import agent

    calls = {"count": 0}

    async def fake_call_chat(account, messages, model, stream, extra, api_key):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeStream([_tool_call_chunk("call_nav", "ui_navigate", '{"path":"/mcp-plaza","title":"MCP 广场"}')])
        return FakeStream(_text_chunks("已打开 MCP 广场。"))

    monkeypatch.setattr(agent, "call_chat", fake_call_chat)
    events = _stream_message(client, auth_headers, conversation_id, "打开 MCP 广场")
    navigate = next(data for name, data in events if name == "navigate")
    assert navigate["path"] == "/mcp-plaza"


def test_assistant_requires_configuration(client: TestClient, auth_headers: dict[str, str]) -> None:
    conversation_id = _create_conversation(client, auth_headers)
    response = client.post(
        f"/api/admin/assistant/conversations/{conversation_id}/messages",
        headers=auth_headers,
        json={"content": "你好"},
    )
    assert response.status_code == 409


def test_assistant_permission_split(client: TestClient, auth_headers: dict[str, str]) -> None:
    role = client.post(
        "/api/admin/roles",
        headers=auth_headers,
        json={"name": "助手使用者", "permissions": ["assistant:view"]},
    )
    assert role.status_code == 201, role.text
    created = client.post(
        "/api/admin/users",
        headers=auth_headers,
        json={"username": "assistant-user", "password": "pass-123456", "role_id": role.json()["id"]},
    )
    assert created.status_code == 201, created.text
    token = client.post(
        "/api/admin/login", json={"username": "assistant-user", "password": "pass-123456"}
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/admin/assistant/conversations", headers=headers).status_code == 200
    assert client.post("/api/admin/assistant/conversations", headers=headers, json={}).status_code == 201
    assert client.put("/api/admin/assistant/settings", headers=headers, json={}).status_code == 403
    assert client.get("/api/admin/assistant/models", headers=headers).status_code == 403


def test_assistant_conversation_isolation(client: TestClient, auth_headers: dict[str, str]) -> None:
    conversation_id = _create_conversation(client, auth_headers)
    role = client.post(
        "/api/admin/roles",
        headers=auth_headers,
        json={"name": "另一个助手用户", "permissions": ["assistant:view"]},
    )
    assert role.status_code == 201, role.text
    client.post(
        "/api/admin/users",
        headers=auth_headers,
        json={"username": "assistant-other", "password": "pass-123456", "role_id": role.json()["id"]},
    )
    token = client.post(
        "/api/admin/login", json={"username": "assistant-other", "password": "pass-123456"}
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get(f"/api/admin/assistant/conversations/{conversation_id}", headers=headers)
    assert response.status_code == 404


def test_assistant_skips_remaining_calls_while_awaiting_approval(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    account = _create_account(client, auth_headers)
    _configure(client, auth_headers, account["id"])
    conversation_id = _create_conversation(client, auth_headers)

    from app.assistant import agent

    calls = {"count": 0}

    async def fake_call_chat(account, messages, model, stream, extra, api_key):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeStream(
                [
                    _tool_call_chunk(
                        "call_write",
                        "api_call",
                        '{"api_id":"POST /api/admin/roles","body":{"name":"并行审批角色","permissions":["logs:view"]}}',
                        index=0,
                    ),
                    _tool_call_chunk(
                        "call_read",
                        "api_call",
                        '{"api_id":"GET /api/admin/roles"}',
                        index=1,
                    ),
                ]
            )
        return FakeStream(_text_chunks("已处理。"))

    monkeypatch.setattr(agent, "call_chat", fake_call_chat)
    events = _stream_message(client, auth_headers, conversation_id, "创建角色并列出角色")
    approval = next(data["approval"] for name, data in events if name == "approval_required")
    skipped = next(
        data
        for name, data in events
        if name == "tool_result" and data["id"] == "call_read"
    )
    assert skipped["ok"] is False
    assert "等待审批" in skipped["error"]

    def call_ids() -> set[str]:
        detail = client.get(
            f"/api/admin/assistant/conversations/{conversation_id}", headers=auth_headers
        ).json()
        return {
            call["id"]
            for message in detail["messages"]
            for call in message["tool_calls"]
        }

    assert "call_read" in call_ids()
    _stream_decision(client, auth_headers, approval["id"], "approve")
    assert {"call_read", "call_write"} <= call_ids()


def test_assistant_navigation_rejects_external_path(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    account = _create_account(client, auth_headers)
    _configure(client, auth_headers, account["id"])
    conversation_id = _create_conversation(client, auth_headers)

    from app.assistant import agent

    calls = {"count": 0}

    async def fake_call_chat(account, messages, model, stream, extra, api_key):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeStream([_tool_call_chunk("call_bad_nav", "ui_navigate", '{"path":"https://example.com"}')])
        return FakeStream(_text_chunks("请使用站内页面。"))

    monkeypatch.setattr(agent, "call_chat", fake_call_chat)
    events = _stream_message(client, auth_headers, conversation_id, "打开外部站点")
    result = next(data for name, data in events if name == "tool_result")
    assert result["ok"] is False
    assert not any(name == "navigate" for name, _ in events)


def test_load_llm_messages_fills_missing_tool_results(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    from app.assistant.store import append_message, load_llm_messages
    from app.db import get_session_factory

    conversation_id = _create_conversation(client, auth_headers)
    session = get_session_factory()()
    try:
        append_message(
            session,
            conversation_id,
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "call_missing",
                    "type": "function",
                    "function": {"name": "api_call", "arguments": '{"api_id":"GET /api/admin/roles"}'},
                }
            ],
        )
        session.commit()
        messages = load_llm_messages(session, conversation_id, 50)
    finally:
        session.close()

    assert messages[0]["role"] == "assistant"
    assert messages[1]["role"] == "tool"
    assert messages[1]["tool_call_id"] == "call_missing"
    assert "缺失" in messages[1]["content"]


def test_assistant_approval_execution_failure_is_recorded(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    account = _create_account(client, auth_headers)
    _configure(client, auth_headers, account["id"])
    conversation_id = _create_conversation(client, auth_headers)

    from app.assistant import agent

    calls = {"count": 0}

    async def fake_call_chat(account, messages, model, stream, extra, api_key):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeStream(
                [
                    _tool_call_chunk(
                        "call_invalid_role",
                        "api_call",
                        '{"api_id":"POST /api/admin/roles","body":{"name":"","permissions":[]}}',
                    )
                ]
            )
        return FakeStream(_text_chunks("创建失败，请检查角色名称。"))

    monkeypatch.setattr(agent, "call_chat", fake_call_chat)
    events = _stream_message(client, auth_headers, conversation_id, "创建空角色")
    approval = next(data["approval"] for name, data in events if name == "approval_required")

    decision_events = _stream_decision(client, auth_headers, approval["id"], "approve")
    result = next(data for name, data in decision_events if name == "tool_result")
    assert result["ok"] is False
    assert result["error"]

    detail = client.get(
        f"/api/admin/assistant/conversations/{conversation_id}", headers=auth_headers
    ).json()
    approval_state = next(
        call["approval"]["status"]
        for message in detail["messages"]
        for call in message["tool_calls"]
        if call["id"] == "call_invalid_role" and call.get("approval")
    )
    assert approval_state == "failed"
    assert detail["messages"][-1]["role"] == "assistant"


def test_assistant_ignores_reasoning_after_answer_text(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    account = _create_account(client, auth_headers)
    _configure(client, auth_headers, account["id"])
    conversation_id = _create_conversation(client, auth_headers)

    from app.assistant import agent

    async def fake_call_chat(account, messages, model, stream, extra, api_key):
        return FakeStream(
            [
                {"choices": [{"delta": {"reasoning_content": "先看用户意图。"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": "答案"}, "finish_reason": None}]},
                {"choices": [{"delta": {"reasoning_content": "这句是噪声。"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": "继续。"}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
            ]
        )

    monkeypatch.setattr(agent, "call_chat", fake_call_chat)
    events = _stream_message(client, auth_headers, conversation_id, "回答我")
    reasoning = "".join(data["delta"] for name, data in events if name == "reasoning")
    assert reasoning == "先看用户意图。"

    detail = client.get(
        f"/api/admin/assistant/conversations/{conversation_id}", headers=auth_headers
    ).json()
    assistant = next(message for message in detail["messages"] if message["role"] == "assistant")
    assert assistant["content"] == "答案继续。"
    assert assistant["reasoning"] == "先看用户意图。"

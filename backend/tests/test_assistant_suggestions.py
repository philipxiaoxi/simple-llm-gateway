from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.assistant import suggestions
from app.db import get_session_factory
from app.models import AssistantConversation, AssistantMessage


def _seed_conversation(message_count: int = 1, *, chars: int = 600) -> str:
    session = get_session_factory()()
    try:
        conversation = AssistantConversation(id="suggestion-test", admin_id=1, title="建议测试会话")
        session.add(conversation)
        for index in range(message_count):
            session.add(
                AssistantMessage(
                    conversation_id=conversation.id,
                    role="user" if index % 2 == 0 else "assistant",
                    content=f"消息{index}" + "内" * chars,
                )
            )
        session.commit()
        return conversation.id
    finally:
        session.close()


def test_suggestions_defaults_without_history(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/admin/assistant/suggestions", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "default"
    assert body["items"]


def test_parse_suggestions_caps_and_strips_fence() -> None:
    raw = "```json\n" + json.dumps(
        {"items": [{"label": f"标签{i}", "prompt": f"指令{i}"} for i in range(8)]}, ensure_ascii=False
    ) + "\n```"
    items = suggestions.parse_suggestions(raw)
    assert len(items) == 6
    assert items[0] == {"label": "标签0", "prompt": "指令0"}
    assert suggestions.parse_suggestions("不是 JSON") == []


def test_history_text_is_limited_to_5000_chars(client: TestClient, auth_headers: dict[str, str]) -> None:
    _seed_conversation(message_count=12)
    session = get_session_factory()()
    try:
        text = suggestions.build_history_text(session, 1)
    finally:
        session.close()
    assert "用户：" in text
    assert len(text) <= suggestions.MAX_HISTORY_CHARS
    assert text.endswith("内" * 20)


def test_suggestions_are_cached_per_history(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    _seed_conversation(message_count=4)
    suggestions._LOCKS.clear()
    calls = {"count": 0}

    async def fake_generate(runtime, history: str) -> list[dict[str, str]]:
        calls["count"] += 1
        assert history.strip()
        return [{"label": "继续上次任务", "prompt": "继续上次的抖音下载任务"}]

    monkeypatch.setattr(suggestions, "_generate_with_llm", fake_generate)
    runtime = SimpleNamespace(account=SimpleNamespace(id=1), model="test-model")

    session = get_session_factory()()
    try:
        first_items, first_source = asyncio.run(suggestions.build_suggestions(session, 1, runtime))
    finally:
        session.close()

    # 换一个全新会话读取，验证缓存已经落库而不是只在进程内存里
    session = get_session_factory()()
    try:
        second_items, second_source = asyncio.run(suggestions.build_suggestions(session, 1, runtime))
    finally:
        session.close()

    assert first_source == second_source == "history"
    assert first_items == second_items == [{"label": "继续上次任务", "prompt": "继续上次的抖音下载任务"}]
    assert calls["count"] == 1


def test_suggestions_regenerate_when_history_changes(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    _seed_conversation(message_count=2)
    suggestions._LOCKS.clear()
    calls = {"count": 0}

    async def fake_generate(runtime, history: str) -> list[dict[str, str]]:
        calls["count"] += 1
        return [{"label": f"建议{calls['count']}", "prompt": f"继续任务{calls['count']}"}]

    monkeypatch.setattr(suggestions, "_generate_with_llm", fake_generate)
    runtime = SimpleNamespace(account=SimpleNamespace(id=1), model="test-model")

    session = get_session_factory()()
    try:
        asyncio.run(suggestions.build_suggestions(session, 1, runtime))
        session.add(
            AssistantMessage(conversation_id="suggestion-test", role="user", content="新增的一条历史消息")
        )
        session.commit()
        items, source = asyncio.run(suggestions.build_suggestions(session, 1, runtime))
    finally:
        session.close()

    assert source == "history"
    assert items[0]["label"] == "建议2"
    assert calls["count"] == 2

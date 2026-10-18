import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class FakeResponse:
    def model_dump(self) -> dict:
        return {
            "id": "chatcmpl-test",
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


def test_dashboard_counts(client: TestClient, auth_headers: dict[str, str]) -> None:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    ).json()
    key = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account["id"]},
    ).json()["key"]
    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
        )
    dashboard = client.get("/api/admin/dashboard", headers=auth_headers)
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["account_count"] == 1
    assert body["today_requests"] >= 1
    assert body["today_tokens"] >= 2
    assert body["total_requests"] >= 1
    assert body["total_tokens"] >= 2
    assert body["benchmark_count"] == 0
    assert body["key_count"] == 1
    assert body["tool_count"] >= 0
    assert body["agent_count"] == 0
    assert body["agent_online_count"] == 0
    assert body["leaderboard_top"] == []
    assert body["benchmark_speed_top"] == []


def test_logs_are_paginated(client: TestClient, auth_headers: dict[str, str]) -> None:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    ).json()
    key = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account["id"]},
    ).json()["key"]
    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
        for index in range(3):
            client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "deepseek-chat", "messages": [{"role": "user", "content": f"hi-{index}"}]},
            )

    first = client.get("/api/admin/logs", headers=auth_headers, params={"page": 1, "page_size": 2})
    assert first.status_code == 200
    body = first.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2
    second = client.get("/api/admin/logs", headers=auth_headers, params={"page": 2, "page_size": 2})
    assert len(second.json()["items"]) == 1
    ids = {item["id"] for item in body["items"] + second.json()["items"]}
    assert len(ids) == 3
    assert body["items"][0]["api_key_name"] == "k"
    assert body["items"][0]["account_name"] == "DS"
    assert body["items"][0]["account_source"] == "upstream"


def test_log_messages_are_paginated_newest_first(client: TestClient, auth_headers: dict[str, str]) -> None:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    ).json()
    key = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account["id"]},
    ).json()["key"]
    history = [{"role": "user", "content": f"turn-{index}"} for index in range(5)]
    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "messages": history},
        )
    log_id = client.get("/api/admin/logs", headers=auth_headers).json()["items"][0]["id"]
    header = client.get(f"/api/admin/logs/{log_id}", headers=auth_headers, params={"include_bodies": False})
    assert header.status_code == 200
    assert header.json()["request_body"] is None
    assert header.json()["response_body"] is None

    first = client.get(
        f"/api/admin/logs/{log_id}/messages",
        headers=auth_headers,
        params={"page": 1, "page_size": 3},
    )
    assert first.status_code == 200
    body = first.json()
    assert body["total"] == 6
    assert body["page"] == 1
    assert [item["role"] for item in body["items"]] == ["assistant", "user", "user"]
    assert [item["seq"] for item in body["items"]] == [5, 4, 3]
    assert body["items"][0]["content_preview"] == "ok"
    assert body["items"][0]["content_truncated"] is False
    assert [item["content_preview"] for item in body["items"][1:]] == ["turn-4", "turn-3"]

    second = client.get(
        f"/api/admin/logs/{log_id}/messages",
        headers=auth_headers,
        params={"page": 2, "page_size": 3},
    )
    assert [item["content_preview"] for item in second.json()["items"]] == ["turn-2", "turn-1", "turn-0"]


def test_log_message_detail_range_export_and_reasoning(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    ).json()
    key = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account["id"]},
    ).json()["key"]
    history = [{"role": "user", "content": f"turn-{index}"} for index in range(3)]
    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "messages": history},
        )
    log_id = client.get("/api/admin/logs", headers=auth_headers).json()["items"][0]["id"]

    # 列表默认只带预览，不带全文
    listed = client.get(
        f"/api/admin/logs/{log_id}/messages", headers=auth_headers, params={"page": 1, "page_size": 20}
    ).json()
    assert "content" not in listed["items"][0] or listed["items"][0]["content"] is None
    assert listed["items"][0]["content_length"] >= 0

    # 范围 + detail 返回全文
    ranged = client.get(
        f"/api/admin/logs/{log_id}/messages",
        headers=auth_headers,
        params={"from_seq": 0, "to_seq": 1, "detail": True},
    ).json()
    assert ranged["total"] == 2
    assert {item["seq"] for item in ranged["items"]} == {0, 1}
    assert all(item["content"] is not None for item in ranged["items"])

    # 单条全文端点
    single = client.get(f"/api/admin/logs/{log_id}/messages/0", headers=auth_headers)
    assert single.status_code == 200
    assert single.json()["content"] == "turn-0"
    assert client.get(f"/api/admin/logs/{log_id}/messages/999", headers=auth_headers).status_code == 404

    # 导出端点
    exported = client.get(f"/api/admin/logs/{log_id}/export", headers=auth_headers)
    assert exported.status_code == 200
    assert len(exported.json()["messages"]) == 4
    assert exported.json()["reasoning"] == {}

    # 思考端点：未命中 404
    assert client.get(f"/api/admin/logs/{log_id}/reasoning/nope", headers=auth_headers).status_code == 404

    # 轮次轮廓：user 消息划分轮次，序号绝对
    outline = client.get(f"/api/admin/logs/{log_id}/turns", headers=auth_headers).json()["items"]
    assert len(outline) == 3
    assert outline[0]["index"] == 1
    assert outline[0]["anchor_seq"] == 0
    assert outline[0]["message_count"] == 1
    assert outline[2]["index"] == 3
    assert outline[2]["anchor_seq"] == 2
    assert outline[2]["to_seq"] == 3
    assert outline[2]["message_count"] == 2

    # 详情：模型解析链按请求时记录（无模型目录时只记录请求名）+ 截断标记
    detail = client.get(f"/api/admin/logs/{log_id}", headers=auth_headers).json()
    assert detail["model_chain"]["requested"] == "deepseek-chat"
    assert detail["model_chain"]["upstream"] == "deepseek-chat"
    assert detail["model_chain"]["resolved"] is False
    assert detail["storage_truncated"] is False
    assert detail["reasoning_summary"] is None

    # 请求头落库前脱敏：凭据打码，原始 API Key 不得出现在详情里
    stored_headers = {name.lower(): value for name, value in detail["request_headers"].items()}
    assert stored_headers["authorization"] == "<redacted>"
    assert key not in json.dumps(detail["request_headers"], ensure_ascii=False)


def _make_raw_log(session, **kwargs) -> int:
    from app.models import RequestLog

    log = RequestLog(
        account_id=1,
        account_name="DS",
        account_source="upstream",
        api_key_id=7,
        api_key_name="k",
        protocol="openai_chat",
        model="deepseek-chat",
        stream=False,
        status="success",
        http_status=200,
        latency_ms=1,
        **kwargs,
    )
    session.add(log)
    session.flush()
    return int(log.id)


def test_rolling_window_keeps_system_head_and_latest(client: TestClient, monkeypatch) -> None:
    """滚动窗口：超上限后保留头部系统消息 + 最新 N 条，最旧普通消息被清理。"""
    import json

    from sqlalchemy import func, select

    from app.db import get_session_factory
    from app.models import RequestLogMessage
    from app.services import conversation
    from app.services.conversation import (
        append_log_messages,
        load_log_messages_head,
        load_log_messages_tail,
        new_messages_to_store,
    )

    monkeypatch.setattr(conversation, "MAX_LOG_MESSAGES", 40)
    session = get_session_factory()()
    try:
        log_id = _make_raw_log(session)
        append_log_messages(session, log_id, [{"role": "system", "content": "sys-head"}])
        append_log_messages(session, log_id, [{"role": "user", "content": "u-0"}, {"role": "assistant", "content": "a-0"}])
        for index in range(1, 60):
            append_log_messages(session, log_id, [{"role": "user", "content": f"u-{index}"}])
        session.commit()

        rows = session.scalars(
            select(RequestLogMessage).where(RequestLogMessage.log_id == log_id).order_by(RequestLogMessage.seq)
        ).all()
        contents = [json.loads(row.content_json)["content"] for row in rows]
        assert len(rows) == 40
        assert rows[0].role == "system" and contents[0] == "sys-head"
        assert contents[1:] == [f"u-{index}" for index in range(21, 60)]
        assert "u-0" not in contents and "a-0" not in contents and "u-20" not in contents

        # 续写去重：客户端重发完整历史（含已滚动清理的中间部分）时只补录最后一条
        inbound = [{"role": "system", "content": "sys-head"}] + [
            {"role": "user", "content": f"u-{index}"} for index in range(0, 60)
        ] + [{"role": "user", "content": "u-final"}]
        head = load_log_messages_head(session, log_id, len(inbound))
        tail = load_log_messages_tail(session, log_id, 1)
        to_store = new_messages_to_store(tail, inbound, None, head_loader=lambda: head)
        assert len(to_store) == 1 and to_store[0]["content"] == "u-final"
        append_log_messages(session, log_id, to_store)
        session.commit()

        count = session.scalar(select(func.count()).where(RequestLogMessage.log_id == log_id))
        assert count == 40
        rows = session.scalars(
            select(RequestLogMessage).where(RequestLogMessage.log_id == log_id).order_by(RequestLogMessage.seq)
        ).all()
        contents = [json.loads(row.content_json)["content"] for row in rows]
        assert contents[0] == "sys-head"
        assert contents[-1] == "u-final"
        assert "u-21" not in contents and "u-22" in contents
    finally:
        session.close()


def test_message_dedup_without_rolling(client: TestClient) -> None:
    """未滚动时续写去重行为保持：全量重发不再入库，新消息只补最后一条。"""
    from app.db import get_session_factory
    from app.services.conversation import (
        append_log_messages,
        load_log_messages_head,
        load_log_messages_tail,
        new_messages_to_store,
    )

    session = get_session_factory()()
    try:
        log_id = _make_raw_log(session)
        append_log_messages(session, log_id, [{"role": "system", "content": "sys-head"}])
        append_log_messages(session, log_id, [{"role": "user", "content": "u-0"}, {"role": "assistant", "content": "a-0"}])
        session.commit()

        # 全量重发：无新增
        inbound = [
            {"role": "system", "content": "sys-head"},
            {"role": "user", "content": "u-0"},
            {"role": "assistant", "content": "a-0"},
        ]
        head = load_log_messages_head(session, log_id, len(inbound))
        tail = load_log_messages_tail(session, log_id, 1)
        assert new_messages_to_store(tail, inbound, None, head_loader=lambda: head) == []

        # 追加一条新用户消息
        inbound = inbound[:-1] + [{"role": "user", "content": "u-1"}]
        head = load_log_messages_head(session, log_id, len(inbound))
        tail = load_log_messages_tail(session, log_id, 1)
        to_store = new_messages_to_store(tail, inbound, None, head_loader=lambda: head)
        assert len(to_store) == 1 and to_store[0]["content"] == "u-1"
    finally:
        session.close()

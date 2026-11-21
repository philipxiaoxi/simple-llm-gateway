import json
from threading import Event
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


class FakeResponse:
    def model_dump(self) -> dict:
        return {
            "id": "chatcmpl-test",
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


@pytest.mark.parametrize("request_content_capture", [True], indirect=True)
def test_content_writer_can_stall_without_blocking_json_or_sse(client, auth_headers, monkeypatch):
    from app.services import log_content

    account = client.post("/api/admin/accounts", headers=auth_headers,
                          json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"}).json()
    key = client.post("/api/admin/keys", headers=auth_headers,
                      json={"name": "k", "account_id": account["id"]}).json()["key"]
    entered, release = Event(), Event()

    def blocked(*_):
        entered.set()
        assert release.wait(8), "Response waited for the content writer"

    monkeypatch.setattr(log_content, "_write_content", blocked)
    headers = {"Authorization": f"Bearer {key}"}
    body = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "hello"}]}

    async def chunks():
        yield {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    try:
        with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
            response = client.post("/v1/chat/completions", headers=headers, json=body)
        assert response.status_code == 200 and entered.wait(2)
        with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=chunks())):
            response = client.post("/v1/chat/completions", headers=headers, json={**body, "stream": True})
        assert response.status_code == 200 and "data: [DONE]" in response.text
        assert not release.is_set() and log_content._queue.unfinished_tasks >= 2
        logs = client.get("/api/admin/logs", headers=auth_headers).json()
        assert logs["total"] == 2 and all(row["total_tokens"] == 2 for row in logs["items"])
    finally:
        release.set()


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


def test_details_are_readable_while_archive_is_being_created(client, auth_headers):
    from app.db import get_session_factory
    from app.models import RequestLog
    from app.services.log_content import archive_path

    with get_session_factory()() as session:
        log = RequestLog(account_id=1, protocol="openai_chat", status="success", http_status=200,
                         request_body='{"legacy": true}')
        session.add(log)
        session.flush()
        log_id = log.id
        session.commit()
    archive_path().touch(exist_ok=False)
    detail = client.get(f"/api/admin/logs/{log_id}?include_bodies=true", headers=auth_headers)
    assert detail.status_code == 200 and detail.json()["request_body"] == {"legacy": True}


def test_dashboard_merges_request_log_scalar_queries(client: TestClient, auth_headers: dict[str, str]) -> None:
    """仪表盘标量统计只读取今天的 request_logs 分区，并额外读取日聚合范围起点。"""
    from sqlalchemy import event

    from app.db import get_engine

    request_log_queries = {"count": 0}

    def before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        if "request_logs" in statement and statement.lstrip().upper().startswith("SELECT"):
            request_log_queries["count"] += 1

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        response = client.get("/api/admin/dashboard", headers=auth_headers)
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    assert response.status_code == 200, response.text
    assert request_log_queries["count"] == 2


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


@pytest.mark.parametrize("request_content_capture", [True], indirect=True)
def test_log_messages_are_paginated_newest_first(
    client: TestClient, auth_headers: dict[str, str], wait_for_log_content
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
    history = [{"role": "user", "content": f"turn-{index}"} for index in range(5)]
    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "messages": history},
        )
    log_id = client.get("/api/admin/logs", headers=auth_headers).json()["items"][0]["id"]
    wait_for_log_content()
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
    newest_seq = body["items"][0]["seq"]
    assert [item["seq"] for item in body["items"]] == [newest_seq, newest_seq - 1, newest_seq - 2]
    assert body["items"][0]["content_preview"] == "ok"
    assert body["items"][0]["content_truncated"] is False
    assert [item["content_preview"] for item in body["items"][1:]] == ["turn-4", "turn-3"]

    second = client.get(
        f"/api/admin/logs/{log_id}/messages",
        headers=auth_headers,
        params={"page": 2, "page_size": 3},
    )
    assert [item["content_preview"] for item in second.json()["items"]] == ["turn-2", "turn-1", "turn-0"]
    assert [item["seq"] for item in second.json()["items"]] == [newest_seq - 3, newest_seq - 4, newest_seq - 5]


@pytest.mark.parametrize("request_content_capture", [True], indirect=True)
def test_log_message_detail_range_export_and_reasoning(
    client: TestClient, auth_headers: dict[str, str], wait_for_log_content
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
    wait_for_log_content()

    # 列表默认只带预览，不带全文
    listed = client.get(
        f"/api/admin/logs/{log_id}/messages", headers=auth_headers, params={"page": 1, "page_size": 20}
    ).json()
    assert "content" not in listed["items"][0] or listed["items"][0]["content"] is None
    assert listed["items"][0]["content_length"] >= 0
    first_seq = listed["items"][-1]["seq"]

    # 范围 + detail 返回全文
    ranged = client.get(
        f"/api/admin/logs/{log_id}/messages",
        headers=auth_headers,
        params={"from_seq": first_seq, "to_seq": first_seq + 1, "detail": True},
    ).json()
    assert ranged["total"] == 2
    assert [item["seq"] for item in ranged["items"]] == [first_seq + 1, first_seq]
    assert [item["content"] for item in ranged["items"]] == ["turn-1", "turn-0"]

    # 单条全文端点
    single = client.get(f"/api/admin/logs/{log_id}/messages/{first_seq}", headers=auth_headers)
    assert single.status_code == 200
    assert single.json()["content"] == "turn-0"
    assert client.get(f"/api/admin/logs/{log_id}/messages/{first_seq + 4}", headers=auth_headers).status_code == 404

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
    assert outline[0]["anchor_seq"] == first_seq
    assert outline[0]["message_count"] == 1
    assert outline[2]["index"] == 3
    assert outline[2]["anchor_seq"] == first_seq + 2
    assert outline[2]["to_seq"] == first_seq + 3
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


def test_legacy_rolling_window_keeps_system_head_and_latest(client: TestClient, monkeypatch) -> None:
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


def test_live_message_dedup_without_rolling(client: TestClient, auth_headers: dict[str, str]) -> None:
    from app.db import get_session_factory
    from app.services.log_content import _write_content

    with get_session_factory()() as session:
        log_id = _make_raw_log(session)
        session.commit()

    initial = [{"role": "system", "content": "sys-head"}, {"role": "user", "content": "u-0"}]
    assistant = {"role": "assistant", "content": "a-0"}
    response = {"choices": [{"message": assistant}]}
    _write_content(log_id, "openai_chat", {"messages": initial}, response, None, None)
    _write_content(log_id, "openai_chat", {"messages": initial}, response, None, None)

    inbound = initial + [assistant, {"role": "user", "content": "u-1"}]
    _write_content(log_id, "openai_chat", {"messages": inbound}, None, None, None)
    exported = client.get(f"/api/admin/logs/{log_id}/export", headers=auth_headers)
    assert exported.status_code == 200
    assert [(item["role"], item["content"]) for item in exported.json()["messages"]] == [
        (message["role"], message["content"]) for message in inbound
    ]

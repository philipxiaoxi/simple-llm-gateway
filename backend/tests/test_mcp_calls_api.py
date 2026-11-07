from __future__ import annotations

from fastapi.testclient import TestClient


def _create_key(client: TestClient, auth_headers: dict[str, str], name: str) -> str:
    response = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": name, "capability_ids": ["knowledge"]},
    )
    assert response.status_code == 201, response.text
    return response.json()["key"]


def _seed_calls(client: TestClient, auth_headers: dict[str, str]) -> str:
    key = _create_key(client, auth_headers, "calls")
    auth = {"Authorization": f"Bearer {key}"}

    ok = client.post("/v1/capabilities/knowledge/list", headers=auth, json={})
    assert ok.status_code == 200, ok.text

    failed = client.post(
        "/v1/capabilities/knowledge/search",
        headers=auth,
        json={"kb_id": "missing-kb", "query": "anything", "mode": "fulltext"},
    )
    assert failed.status_code == 404, failed.text
    return key


def test_call_logs_paginated(client: TestClient, auth_headers: dict[str, str]):
    _seed_calls(client, auth_headers)

    first = client.get("/api/admin/mcp/calls?limit=1", headers=auth_headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["limit"] == 1
    assert body["offset"] == 0
    assert len(body["items"]) == 1
    assert body["total"] >= 2

    second = client.get("/api/admin/mcp/calls?limit=1&offset=1", headers=auth_headers).json()
    assert len(second["items"]) == 1
    assert second["items"][0]["id"] != body["items"][0]["id"]


def _create_key_with(client: TestClient, auth_headers: dict[str, str], name: str, capabilities: list[str]) -> str:
    response = client.post(
        "/api/admin/mcp/keys",
        headers=auth_headers,
        json={"name": name, "capability_ids": capabilities},
    )
    assert response.status_code == 201, response.text
    return response.json()["key"]


def test_site_rest_writes_call_logs(client: TestClient, auth_headers: dict[str, str]) -> None:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("index.html", b"<p>logged</p>")
    key = _create_key_with(client, auth_headers, "site-calls", ["site"])
    headers = {"Authorization": f"Bearer {key}"}
    created = client.post(
        "/v1/sites",
        headers=headers,
        data={"slug": "logged-site"},
        files={"file": ("site.zip", buffer.getvalue(), "application/zip")},
    )
    assert created.status_code == 202, created.text
    listed = client.get("/v1/sites", headers=headers)
    assert listed.status_code == 200, listed.text
    denied = client.get("/v1/sites", headers={"Authorization": f"Bearer {_create_key(client, auth_headers, 'kb-only')}"})
    assert denied.status_code == 403

    logs = client.get("/api/admin/mcp/calls?capability_id=site", headers=auth_headers).json()
    operations = {item["operation"] for item in logs["items"]}
    assert {"deploy", "list"} <= operations
    assert any(item["success"] is True and item["operation"] == "deploy" for item in logs["items"])
    assert any(item["success"] is False and item["operation"] == "list" for item in logs["items"])


def test_diagram_rest_writes_call_logs(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    from app.capabilities.diagram import renderer

    def fake_run(diagram_type, source_path, out_path, quality, sandbox):
        out_path.write_text("<!doctype html><html><body>logged</body></html>", encoding="utf-8")
        return 0, '{"ok": true, "diagnostics": []}', ""

    monkeypatch.setattr(renderer, "_run_cli", fake_run)
    key = _create_key_with(client, auth_headers, "diagram-calls", ["diagram"])
    headers = {"Authorization": f"Bearer {key}"}
    created = client.post(
        "/v1/diagrams",
        headers=headers,
        json={
            "type": "architecture",
            "source": {
                "schema_version": 1,
                "diagram_type": "architecture",
                "meta": {"title": "logged", "output": "logged.html"},
                "components": [{"id": "a", "type": "external", "label": "A"}],
            },
            "slug": "logged-diagram",
        },
    )
    assert created.status_code == 202, created.text
    detail = client.get("/v1/diagrams/logged-diagram", headers=headers)
    assert detail.status_code == 200, detail.text

    logs = client.get("/api/admin/mcp/calls?capability_id=diagram", headers=auth_headers).json()
    operations = {item["operation"] for item in logs["items"]}
    assert {"create", "status"} <= operations
    assert all(item["capability_id"] == "diagram" for item in logs["items"])


def test_call_detail_captures_request_and_response(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _create_key(client, auth_headers, "detail")
    auth = {"Authorization": f"Bearer {key}"}
    ok = client.post("/v1/capabilities/knowledge/list", headers=auth, json={})
    assert ok.status_code == 200, ok.text

    listed = client.get("/api/admin/mcp/calls?capability_id=knowledge&success=true", headers=auth_headers).json()
    item = listed["items"][0]
    assert item["transport"] == "rest"
    assert item["http_status"] == 200

    detail = client.get(f"/api/admin/mcp/calls/{item['id']}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["id"] == item["id"]
    assert body["request_body"] == "{}"
    assert body["response_body"]
    assert body["request_headers"]
    assert body["user_agent"]

    missing = client.get("/api/admin/mcp/calls/999999", headers=auth_headers)
    assert missing.status_code == 404


def test_call_detail_handles_legacy_rows(client: TestClient, auth_headers: dict[str, str]) -> None:
    from app.db import get_session_factory
    from app.models import McpCallLog

    session = get_session_factory()()
    try:
        row = McpCallLog(capability_id="knowledge", operation="list", success=True, latency_ms=5)
        session.add(row)
        session.commit()
        call_id = row.id
    finally:
        session.close()

    detail = client.get(f"/api/admin/mcp/calls/{call_id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    # 历史记录未采集详情，字段为 null，前端据此降级展示
    assert body["transport"] is None
    assert body["request_body"] is None
    assert body["response_body"] is None
    assert body["client_ip"] is None


def test_mcp_tool_failure_is_logged(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _create_key(client, auth_headers, "mcp-fail")
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    response = client.post(
        "/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "knowledge_search",
                "arguments": {"kb_id": "missing", "query": "hello", "mode": "fulltext"},
            },
        },
    )
    assert response.status_code == 200, response.text

    logs = client.get(
        "/api/admin/mcp/calls?capability_id=knowledge&success=false", headers=auth_headers
    ).json()
    assert logs["total"] >= 1
    item = logs["items"][0]
    assert item["transport"] == "mcp"
    assert item["error_type"]
    assert item["http_status"] == 404

    detail = client.get(f"/api/admin/mcp/calls/{item['id']}", headers=auth_headers).json()
    assert detail["request_body"] and "missing" in detail["request_body"]


def test_call_logs_filters(client: TestClient, auth_headers: dict[str, str]):
    _seed_calls(client, auth_headers)

    success = client.get("/api/admin/mcp/calls?success=true", headers=auth_headers).json()
    assert success["total"] >= 1
    assert all(item["success"] is True for item in success["items"])

    failed = client.get("/api/admin/mcp/calls?success=false", headers=auth_headers).json()
    assert failed["total"] >= 1
    assert all(item["success"] is False for item in failed["items"])
    assert all(item["error_message"] for item in failed["items"])

    by_capability = client.get("/api/admin/mcp/calls?capability_id=knowledge", headers=auth_headers).json()
    assert by_capability["total"] >= 2

    by_keyword = client.get("/api/admin/mcp/calls?q=search", headers=auth_headers).json()
    assert by_keyword["total"] >= 1
    assert all(item["operation"] == "search" for item in by_keyword["items"])

    none = client.get("/api/admin/mcp/calls?capability_id=not-exists", headers=auth_headers).json()
    assert none["total"] == 0
    assert none["items"] == []


def _shanghai_noon_utc():
    from datetime import UTC, datetime

    from app.clock import SHANGHAI_TZ

    return datetime.now(SHANGHAI_TZ).replace(hour=12, minute=0, second=0, microsecond=0).astimezone(UTC).replace(tzinfo=None)


def test_call_stats_summary(client: TestClient, auth_headers: dict[str, str]) -> None:
    from app.db import get_session_factory
    from app.models import McpCallLog
    from app.services import mcp_stats

    session = get_session_factory()()
    try:
        now = _shanghai_noon_utc()
        session.add_all(
            [
                McpCallLog(
                    capability_id="pose",
                    operation="search",
                    success=True,
                    latency_ms=20,
                    mcp_key_name="pose-key",
                    created_at=now,
                ),
                McpCallLog(
                    capability_id="pose",
                    operation="get",
                    success=False,
                    latency_ms=80,
                    mcp_key_name="pose-key",
                    error_message="missing",
                    created_at=now,
                ),
                McpCallLog(
                    capability_id="costume",
                    operation="search",
                    success=True,
                    latency_ms=10,
                    mcp_key_name="costume-key",
                    created_at=now,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    # 未刷新时只读快照，返回空统计
    empty = client.get("/api/admin/mcp/calls/summary?days=7", headers=auth_headers)
    assert empty.status_code == 200, empty.text
    assert empty.json()["total"] == 0

    refreshed = client.get("/api/admin/mcp/calls/summary?days=7&refresh=1", headers=auth_headers)
    assert refreshed.status_code == 200, refreshed.text
    body = refreshed.json()
    assert body["total"] == 3
    assert body["success"] == 2
    assert body["failed"] == 1
    assert body["success_rate"] == round(2 / 3, 4)
    assert body["avg_latency_ms"] == 37
    assert len(body["daily"]) == 7
    assert body["daily"][-1]["total"] == 3
    assert len(body["hourly"]) == 24
    # 只有失败的操作才进失败榜，按失败数排序
    assert [item["key"] for item in body["failure_operations"]] == ["pose:get"]
    assert body["failure_operations"][0]["failed"] == 1

    # 耗时分布固定 5 档，且总数与区间调用量一致
    assert [item["key"] for item in body["latency_buckets"]] == [key for key, *_ in mcp_stats.LATENCY_BUCKETS]
    assert sum(item["total"] for item in body["latency_buckets"]) == body["total"]
    assert body["latency_buckets"][0]["total"] == 3  # 10/20/80ms 都落在 < 100ms

    # 慢操作按 P95 排序，带调用量与均值
    slowest = body["slow_operations"][0]
    assert slowest["key"] == "pose:get"
    assert slowest["p95_latency_ms"] == 80
    assert slowest["avg_latency_ms"] == 80

    # 缓存读取不再重算，数据与刷新结果一致
    cached = client.get("/api/admin/mcp/calls/summary?days=7", headers=auth_headers)
    assert cached.json() == body


def test_call_stats_merges_failure_remainder_into_other(client: TestClient, auth_headers: dict[str, str]) -> None:
    from app.db import get_session_factory
    from app.models import McpCallLog
    from app.services import mcp_stats

    session = get_session_factory()()
    try:
        now = _shanghai_noon_utc()
        # 12 个操作里有 9 个失败，失败榜前 6 项之外的 3 个应合并成「其他」
        session.add_all(
            [
                McpCallLog(
                    capability_id="cap",
                    operation=f"op-{index:02d}",
                    success=index >= 9,
                    latency_ms=(index + 1) * 200,
                    mcp_key_name="shared-key",
                    created_at=now,
                )
                for index in range(12)
            ]
        )
        session.commit()
    finally:
        session.close()

    session = get_session_factory()()
    try:
        stats = mcp_stats.refresh_snapshot(session, 7)
    finally:
        session.close()
    assert stats.total == 12
    assert stats.failed == 9
    assert len(stats.failure_operations) == mcp_stats.FAILURE_LIMIT + 1
    assert stats.failure_operations[-1].key == mcp_stats.OTHER_KEY
    assert stats.failure_operations[-1].failed == 3
    assert sum(item.failed for item in stats.failure_operations) == stats.failed
    # 慢操作只保留前 6 项，不合并「其他」
    assert len(stats.slow_operations) == mcp_stats.SLOW_LIMIT
    assert stats.slow_operations[0].key == "cap:op-11"
    assert stats.slow_operations[0].p95_latency_ms == 2400
    assert sum(item.total for item in stats.latency_buckets) == stats.total


def test_snapshot_schema_marks_outdated_payload(client: TestClient, auth_headers: dict[str, str]) -> None:
    from app.db import get_session_factory
    from app.models import McpCallStatsSnapshot
    from app.services import mcp_stats

    session = get_session_factory()()
    try:
        # 旧结构快照没有 schema 标记，启动补偿据此判定需要重算
        session.add(McpCallStatsSnapshot(days=7, payload_json='{"days": 7}'))
        session.commit()
        assert not mcp_stats.snapshot_is_current('{"days": 7}')

        mcp_stats.refresh_snapshot(session, 7)
        row = session.get(McpCallStatsSnapshot, 7)
        assert row is not None and mcp_stats.snapshot_is_current(row.payload_json)
    finally:
        session.close()


def test_call_stats_is_stable_across_scan_chunks(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    from datetime import timedelta

    from app.db import get_session_factory
    from app.models import McpCallLog
    from app.services import mcp_stats
    from app.services.stats_scan import iter_time_chunks

    session = get_session_factory()()
    try:
        now = _shanghai_noon_utc()
        session.add_all(
            [
                McpCallLog(
                    capability_id="pose",
                    operation=f"op-{index}",
                    success=index != 1,
                    latency_ms=100 + index * 50,
                    created_at=now - timedelta(days=index),
                )
                for index in range(5)
            ]
        )
        session.commit()
        baseline = mcp_stats.compute_stats(session, 7)
        monkeypatch.setattr(
            mcp_stats,
            "iter_time_chunks",
            lambda start, end: iter_time_chunks(start, end, days=1),
        )
        chunked = mcp_stats.compute_stats(session, 7)
    finally:
        session.close()

    assert chunked == baseline
    assert chunked.total == 5
    assert chunked.failed == 1


def test_call_stats_job_uses_incremental_rollups(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    from datetime import datetime, timedelta

    from app.clock import SHANGHAI_TZ
    from app.db import get_session_factory
    from app.models import McpCallDailyStats, McpCallLog
    from app.services import mcp_stats

    now = _shanghai_noon_utc()
    old_created = now - timedelta(days=10)
    session = get_session_factory()()
    try:
        session.add(
            McpCallLog(
                capability_id="pose",
                operation="search",
                success=True,
                latency_ms=120,
                created_at=old_created,
            )
        )
        session.commit()

        assert mcp_stats.refresh_all(session) == len(mcp_stats.SNAPSHOT_DAYS)
        old_day = (datetime.now(SHANGHAI_TZ) - timedelta(days=10)).date().isoformat()
        old_row = session.get(McpCallDailyStats, old_day)
        assert old_row is not None and old_row.total == 1
        old_computed_at = old_row.computed_at
        snapshot = mcp_stats.load_snapshot(session, 30)
        full = mcp_stats.compute_stats(session, 30)
        assert snapshot is not None and snapshot == full

        session.add(
            McpCallLog(
                capability_id="pose",
                operation="get",
                success=False,
                latency_ms=240,
                created_at=now,
            )
        )
        session.commit()
        mcp_stats.refresh_all(session)

        # 历史日不重扫，只刷新今天
        old_row = session.get(McpCallDailyStats, old_day)
        assert old_row is not None and old_row.computed_at == old_computed_at
        snapshot = mcp_stats.load_snapshot(session, 30)
        full = mcp_stats.compute_stats(session, 30)
        assert snapshot is not None and snapshot == full
        assert snapshot.total == 2
        assert snapshot.failed == 1
    finally:
        session.close()

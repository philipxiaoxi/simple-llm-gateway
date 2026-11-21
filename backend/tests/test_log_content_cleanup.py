import sqlite3
import time
from contextlib import closing
from threading import Event

import pytest


@pytest.fixture
def migrated(client, auth_headers, monkeypatch):
    from app.config import get_settings
    from app.services import log_content_migration

    source = get_settings().database_path
    with closing(sqlite3.connect(source)) as db, db:
        db.executemany("""
            INSERT INTO request_logs (id, account_id, account_source, protocol, stream, status,
              http_status, total_tokens, latency_ms, turn_count, created_at, request_body,
              response_body, reasoning_json, request_headers)
            VALUES (?, 1, 'upstream', 'openai_chat', 0, 'success', 200, 12, 45, 3, CURRENT_TIMESTAMP,
                    '{"prompt":"test"}', '{}', '{"tool":"thought"}', '{}')
        """, [(1,), (2,)])
        db.execute("""
            INSERT INTO request_log_messages (id, log_id, seq, role, content_json, created_at)
            VALUES (1, 1, 0, 'user', '{"content":"hello"}', CURRENT_TIMESTAMP)
        """)
    monkeypatch.setattr(log_content_migration, "_BATCH_ROWS", 1)
    monkeypatch.setattr(log_content_migration, "_THROTTLE_SECONDS", 0)
    return source


def _state(client, headers, kind, expected):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        response = client.get(f"/api/admin/logs/content-{kind}", headers=headers)
        assert response.status_code == 200, response.text
        state = response.json()
        if state["state"] == expected:
            return state
        assert state["state"] != "failed", state
        time.sleep(0.01)
    pytest.fail(f"Expected {expected}: {state}")


def test_cleanup_requires_confirmation_and_preserves_metadata_and_archive(client, auth_headers, migrated):
    base = "/api/admin/logs/content-cleanup"
    start = {"backup_confirmed": True}
    assert client.post(f"{base}/start", headers=auth_headers, json=start).status_code == 409
    client.post("/api/admin/logs/content-migration/start", headers=auth_headers).raise_for_status()
    _state(client, auth_headers, "migration", "completed")
    assert client.post(f"{base}/start", headers=auth_headers, json={"backup_confirmed": False}).status_code == 422

    client.post(f"{base}/start", headers=auth_headers, json=start).raise_for_status()
    status = _state(client, auth_headers, "cleanup", "completed")
    assert status["cleaned_rows"] == status["total_rows"] == 3
    assert status["cleaned_bytes"] == status["total_bytes"] > 0
    assert status["reusable_bytes"] >= 0
    with closing(sqlite3.connect(migrated)) as db:
        assert db.execute("SELECT total_tokens, turn_count, request_body, response_body, reasoning_json, request_headers "
                          "FROM request_logs ORDER BY id").fetchall() == [(12, 3, None, None, None, None)] * 2
        assert db.execute("SELECT count(*) FROM request_log_messages").fetchone()[0] == 0
    assert client.get("/api/admin/logs/1?include_bodies=true", headers=auth_headers).json()["request_body"] == {"prompt": "test"}
    assert client.get("/api/admin/logs/1/messages/0", headers=auth_headers).json()["content"] == "hello"
    assert client.post(f"{base}/start", headers=auth_headers, json=start).json()["state"] == "completed"


def test_cleanup_pauses_on_archive_mismatch_and_resumes_without_recounting(client, auth_headers, migrated):
    from app.services.log_content import archive_connection

    client.post("/api/admin/logs/content-migration/start", headers=auth_headers).raise_for_status()
    _state(client, auth_headers, "migration", "completed")
    with archive_connection() as db:
        db.execute("UPDATE legacy_log_content SET request_body='bad' WHERE log_id=2")
    base = "/api/admin/logs/content-cleanup"
    payload = {"backup_confirmed": True}
    client.post(f"{base}/start", headers=auth_headers, json=payload).raise_for_status()
    failed = _state(client, auth_headers, "cleanup", "failed")
    assert failed["cleaned_rows"] == 1
    with closing(sqlite3.connect(migrated)) as db:
        assert db.execute("SELECT request_body FROM request_logs ORDER BY id").fetchall() == [(None,), ('{"prompt":"test"}',)]
    with archive_connection() as db:
        db.execute("UPDATE legacy_log_content SET request_body=? WHERE log_id=2", ('{"prompt":"test"}',))
    client.post(f"{base}/start", headers=auth_headers, json=payload).raise_for_status()
    assert _state(client, auth_headers, "cleanup", "completed")["cleaned_rows"] == 3


def test_cleanup_pauses_at_batch_boundary_and_resumes(client, auth_headers, migrated, monkeypatch):
    from app.services import log_content_cleanup as cleanup

    client.post("/api/admin/logs/content-migration/start", headers=auth_headers).raise_for_status()
    _state(client, auth_headers, "migration", "completed")
    entered, release = Event(), Event()
    require_space = cleanup._require_space

    def gated(*args):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        require_space(*args)

    monkeypatch.setattr(cleanup, "_require_space", gated)
    base = "/api/admin/logs/content-cleanup"
    payload = {"backup_confirmed": True}
    try:
        client.post(f"{base}/start", headers=auth_headers, json=payload).raise_for_status()
        assert entered.wait(3)
        client.post(f"{base}/pause", headers=auth_headers).raise_for_status()
    finally:
        release.set()
    assert _state(client, auth_headers, "cleanup", "paused")["cleaned_rows"] == 1
    client.post(f"{base}/start", headers=auth_headers, json=payload).raise_for_status()
    assert _state(client, auth_headers, "cleanup", "completed")["cleaned_rows"] == 3

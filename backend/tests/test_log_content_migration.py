from __future__ import annotations

import errno
import importlib
import sqlite3
import sys
import threading
import time
from contextlib import closing, contextmanager
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def migration(tmp_path, monkeypatch):
    source = tmp_path / "gateway.db"
    archive = tmp_path / "archive.db"

    @contextmanager
    def archive_connection():
        with closing(sqlite3.connect(archive)) as conn, conn:
            conn.row_factory = sqlite3.Row
            yield conn

    # This fixture owns just the SQLite contract, independently of HTTP or archive setup.
    module = "app.services.log_content_migration"
    stubbed = False
    try:
        job = importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if exc.name != "app.services.log_content":
            raise
        helper = ModuleType(exc.name)
        helper.archive_connection = archive_connection
        helper.archive_path = lambda: archive
        monkeypatch.setitem(sys.modules, helper.__name__, helper)
        job = importlib.import_module(module)
        stubbed = True
    monkeypatch.setattr(job, "archive_connection", archive_connection)
    monkeypatch.setattr(job, "archive_path", lambda: archive)
    monkeypatch.setattr(job, "get_settings", lambda: SimpleNamespace(database_path=str(source)))
    monkeypatch.setattr(job, "_BATCH_ROWS", 2)
    monkeypatch.setattr(job, "_BATCH_BYTES", 12)
    monkeypatch.setattr(job, "_THROTTLE_SECONDS", 0)
    monkeypatch.setattr(job, "_free_bytes", lambda _: 100 * 1024 * 1024)
    with closing(sqlite3.connect(source)) as conn:
        conn.executescript("""
            CREATE TABLE request_logs (id INTEGER PRIMARY KEY, request_body TEXT,
                response_body TEXT, request_headers TEXT, reasoning_json TEXT);
            CREATE TABLE request_log_messages (id INTEGER PRIMARY KEY, log_id INTEGER,
                seq INTEGER, role TEXT, content_json TEXT, created_at TEXT);
            INSERT INTO request_logs VALUES
                (-4, '', NULL, NULL, NULL), (0, 'abc', 'xxxxxxxxxxxxxxxxxxxxxxxx', 'head', '{}'),
                (3, NULL, NULL, NULL, NULL), (5, NULL, NULL, 'hdr', NULL);
            INSERT INTO request_log_messages VALUES
                (4, 0, 0, 'assistant', NULL, '2026-11-21'), (8, 0, 1, 'user', '', '2026-11-21');
        """)
        conn.execute("UPDATE request_log_messages SET content_json=? WHERE id=8", ("\u6c34\x00z",))
        conn.commit()
    with archive_connection() as conn:
        conn.executescript("""
            CREATE TABLE legacy_log_content (log_id INTEGER PRIMARY KEY, request_body TEXT,
                response_body TEXT, request_headers TEXT, reasoning_json TEXT);
            CREATE TABLE legacy_log_messages (id INTEGER PRIMARY KEY, log_id INTEGER NOT NULL,
                seq INTEGER NOT NULL, role TEXT NOT NULL, content_json TEXT, created_at TEXT, UNIQUE(log_id,seq));
        """)
    try:
        yield job, source, archive_connection
    finally:
        job.migration_shutdown()
        # Avoid leaking the stubbed imports into later tests (including HTTP integration tests).
        if stubbed:
            sys.modules.pop(module, None)
            del sys.modules["app.services"].log_content_migration


def _wait(job, state):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = job.migration_status()
        if status["state"] == state:
            job._worker.join(timeout=1)
            return status
        if status["state"] in {"failed", "completed"}:
            break
        time.sleep(0.01)
    pytest.fail(f"Expected {state}, got {status}")


def test_migrates_and_verifies_every_field_without_touching_source(migration):
    job, source, archive_connection = migration
    assert job.migration_status()["state"] == "idle"
    assert not job._checkpoint().exists()
    before = source.read_bytes()
    job.migration_start()
    status = _wait(job, "completed")
    assert status["phase"] is status["error"] is status["remaining_seconds"] is None
    assert status["elapsed_seconds"] > 0
    for prefix in ("total", "scanned", "copied", "verified"):
        assert status[f"{prefix}_rows"] == 5
        assert status[f"{prefix}_bytes"] == 74
    assert source.read_bytes() == before
    with archive_connection() as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM legacy_log_content ORDER BY log_id")] == [
            (-4, "", None, None, None), (0, "abc", "x" * 24, "head", "{}"), (5, None, None, "hdr", None),
        ]
        assert [tuple(row) for row in conn.execute("SELECT * FROM legacy_log_messages ORDER BY id")] == [
            (4, 0, 0, "assistant", None, "2026-11-21"), (8, 0, 1, "user", "\u6c34\x00z", "2026-11-21"),
        ]
    assert job.migration_start()["state"] == "completed"


def test_scan_only_reports_totals_without_copying_then_migration_reuses_scan(migration, monkeypatch):
    job, source, archive_connection = migration
    original = source.read_bytes()
    job.migration_start(scan_only=True)
    ready = _wait(job, "ready")
    assert (ready["total_rows"], ready["total_bytes"], ready["copied_rows"]) == (5, 74, 0)
    assert not job.legacy_content_archived()
    assert source.read_bytes() == original
    with archive_connection() as archive:
        assert archive.execute("SELECT count(*) FROM legacy_log_content").fetchone()[0] == 0
    batch = job._batch

    def no_rescan(*args, scan):
        assert not scan
        return batch(*args, scan=scan)

    monkeypatch.setattr(job, "_batch", no_rescan)
    job.migration_start()
    assert _wait(job, "completed")["verified_rows"] == 5


def test_pause_and_restart_preserve_cursor_and_high_water(migration, monkeypatch):
    job, source, archive_connection = migration
    copied = threading.Event()
    resume = threading.Event()

    @contextmanager
    def gated_archive():
        with archive_connection() as conn:
            yield conn
            if not copied.is_set():
                copied.set()
                assert resume.wait(5)

    monkeypatch.setattr(job, "archive_connection", gated_archive)
    job.migration_start()
    try:
        assert copied.wait(5)
        # The archive writer has an open transaction; status and pause use only the sidecar.
        assert job.migration_status()["copied_rows"] == 0
        assert job.migration_start()["state"] == "copying"
        assert job.migration_pause()["state"] == "copying"
    finally:
        resume.set()
    assert _wait(job, "paused")["copied_rows"] == 1

    with closing(sqlite3.connect(source)) as conn, conn:
        conn.execute("INSERT INTO request_logs VALUES (50, 'new', NULL, NULL, NULL)")
        conn.execute("INSERT INTO request_log_messages VALUES (51, 50, 0, 'user', 'new', '')")
    checkpoint = job._checkpoint()
    saved = job._load(checkpoint)
    assert saved["state"] == "paused"
    # An abrupt exit leaves the same cursor with an active state on disk.
    job._save(checkpoint, {**saved, "state": "copying"})
    monkeypatch.setattr(job, "_worker", None)
    interrupted = job.migration_status()
    assert (interrupted["state"], interrupted["copied_rows"]) == ("paused", 1)
    job.migration_start()
    assert _wait(job, "completed")["verified_rows"] == 5
    with archive_connection() as conn:
        assert conn.execute("SELECT max(log_id) FROM legacy_log_content").fetchone()[0] == 5
        assert conn.execute("SELECT max(id) FROM legacy_log_messages").fetchone()[0] == 8


def test_archive_commit_can_be_replayed_after_checkpoint_failure(migration, monkeypatch):
    job, _, archive_connection = migration
    save = job._save

    def fail_after_commit(path, state):
        if state["state"] == "copying" and state["copied_rows"]:
            raise OSError(errno.ENOSPC, "No space left on device")
        save(path, state)

    monkeypatch.setattr(job, "_save", fail_after_commit)
    job.migration_start()
    assert _wait(job, "failed")["copied_rows"] == 0
    with archive_connection() as conn:
        assert conn.execute("SELECT log_id FROM legacy_log_content").fetchone()[0] == -4
    monkeypatch.setattr(job, "_save", save)
    job.migration_start()
    assert _wait(job, "completed")["copied_rows"] == 5


def test_disk_preflight_fails_before_writes_and_start_retries(migration, monkeypatch):
    job, _, archive_connection = migration
    free_bytes = job._free_bytes
    monkeypatch.setattr(job, "_free_bytes", lambda _: 10)
    job.migration_start()
    status = _wait(job, "failed")
    assert status["phase"] == "copying"
    assert status["free_bytes"] == 10
    assert "Insufficient archive disk space" in status["error"]
    assert status["copied_rows"] == 0
    with archive_connection() as conn:
        assert conn.execute("SELECT count(*) FROM legacy_log_content").fetchone()[0] == 0
    monkeypatch.setattr(job, "_free_bytes", free_bytes)
    job.migration_start()
    assert _wait(job, "completed")["verified_rows"] == 5


def test_archive_write_failure_rolls_back_and_keeps_cursor(migration, monkeypatch):
    job, _, archive_connection = migration

    @contextmanager
    def full_archive():
        with archive_connection() as conn:
            yield conn
            raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(job, "archive_connection", full_archive)
    job.migration_start()
    status = _wait(job, "failed")
    assert "disk is full" in status["error"]
    assert status["copied_rows"] == 0
    with archive_connection() as conn:
        assert conn.execute("SELECT count(*) FROM legacy_log_content").fetchone()[0] == 0
    monkeypatch.setattr(job, "archive_connection", archive_connection)
    job.migration_start()
    assert _wait(job, "completed")["verified_rows"] == 5


def test_verification_detects_same_size_corruption_and_retry_repairs_it(migration, monkeypatch):
    job, _, archive_connection = migration
    save = job._save

    def corrupt_before_verify(path, state):
        if state["phase"] == "verifying" and state["table"] == 0 and state["after"] is None:
            with archive_connection() as conn:
                conn.execute("UPDATE legacy_log_content SET request_body='xyz' WHERE log_id=0")
        save(path, state)

    monkeypatch.setattr(job, "_save", corrupt_before_verify)
    job.migration_start()
    assert "Archive verification failed" in _wait(job, "failed")["error"]
    monkeypatch.setattr(job, "_save", save)
    job.migration_start()
    assert _wait(job, "completed")["verified_rows"] == 5

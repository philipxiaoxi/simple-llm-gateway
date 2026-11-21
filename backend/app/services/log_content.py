from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from contextlib import closing, contextmanager
from pathlib import Path
from queue import Empty, Full, Queue
from types import SimpleNamespace

from app.config import get_settings
from app.services.conversation import (
    MAX_LOG_MESSAGES,
    decode_stored_message,
    encode_message_content,
    extract_assistant_message,
    extract_request_messages,
    new_messages_to_store,
)
from app.services.headers_redact import dump_headers
from app.services.reasoning import dump_reasoning_json, parse_reasoning_json

_LOG = logging.getLogger(__name__)
_LIVE_SEQ = 1 << 40
_MAX_EVENT_BYTES = 1024 * 1024
_MAX_LIVE_BYTES = 256 * 1024 * 1024
_REPLAY_TTL = 3600
_REPLAY_LIMIT = 2048
_replay: OrderedDict[tuple, tuple[float, str]] = OrderedDict()
_replay_lock = threading.Lock()
_queue: Queue = Queue(maxsize=32)
_capture_lock = threading.Lock()
_capture_counts: dict[str, int] = {}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS legacy_log_content (
    log_id INTEGER PRIMARY KEY, request_body TEXT, response_body TEXT,
    request_headers TEXT, reasoning_json TEXT
);
CREATE TABLE IF NOT EXISTS legacy_log_messages (
    id INTEGER PRIMARY KEY, log_id INTEGER NOT NULL, seq INTEGER NOT NULL,
    role TEXT NOT NULL, content_json TEXT, created_at TEXT, UNIQUE(log_id, seq)
);
CREATE TABLE IF NOT EXISTS live_log_content (
    log_id INTEGER PRIMARY KEY, request_headers TEXT, reasoning_json TEXT,
    updated_at INTEGER NOT NULL, payload_bytes INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_live_content_updated ON live_log_content(updated_at);
CREATE TABLE IF NOT EXISTS live_log_messages (
    log_id INTEGER NOT NULL REFERENCES live_log_content(log_id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, role TEXT NOT NULL, content_json TEXT, PRIMARY KEY(log_id, seq)
);
"""


def archive_path() -> Path:
    settings = get_settings()
    source = Path(settings.database_path).expanduser().resolve()
    archive = (Path(settings.request_content_path).expanduser().resolve() if settings.request_content_path
               else source.with_name(f"{source.stem}.request-content.db"))
    if archive == source or source.exists() and archive.exists() and archive.samefile(source):
        raise ValueError("Request content database must be separate from the main database.")
    return archive


@contextmanager
def archive_connection():
    path = archive_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
    with closing(sqlite3.connect(path, timeout=1)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(_SCHEMA)
        with db:
            yield db


def _within_budget(*values) -> bool:
    budget, nodes = _MAX_EVENT_BYTES, 4096
    stack = [iter(values)]
    while stack:
        value = next(stack[-1], ...)
        if value is ...:
            stack.pop()
            continue
        nodes -= 1
        budget -= 64
        if isinstance(value, str):
            budget -= len(value) * 4
        elif isinstance(value, bytes):
            budget -= len(value)
        elif isinstance(value, dict):
            stack.append(iter(value.keys()))
            stack.append(iter(value.values()))
        elif isinstance(value, list | tuple):
            stack.append(iter(value))
        if budget < 0 or nodes < 0 or len(stack) > 64:
            return False
    return True


def enqueue_content(log_id, protocol, request, response, headers, reasoning) -> None:
    if _queue.full():
        _count_capture("queue_full")
        return
    partial = response is ... or not _within_budget(request, response, headers, reasoning)
    if partial:
        if response is ... or not _within_budget(response):
            response = None
        if not _within_budget(response, headers, reasoning):
            reasoning = None
        if not _within_budget(response, headers, reasoning):
            headers = None
        original = request if isinstance(request, dict) else {}
        request = {"model": str(original.get("model") or "")[:256]}
        for field in ("messages", "input"):
            history = original.get(field)
            if isinstance(history, list):
                recent = []
                for message in reversed(history[-8:]):
                    if not _within_budget(request, response, headers, reasoning, message, recent):
                        break
                    recent.insert(0, message)
                request[field] = recent
            elif isinstance(history, str) and _within_budget(request, response, headers, reasoning, history):
                request[field] = history
        if not any(request.get(field) for field in ("messages", "input")) and response is None:
            _count_capture("oversized")
            return
    try:
        _queue.put_nowait((log_id, protocol, request, response, headers, reasoning))
    except Full:
        _count_capture("queue_full")
    else:
        if partial:
            _count_capture("partial")


def _count_capture(reason: str) -> None:
    with _capture_lock:
        _capture_counts[reason] = _capture_counts.get(reason, 0) + 1


def capture_diagnostics() -> dict:
    with _capture_lock:
        counts = dict(_capture_counts)
    return {"capture_enabled": get_settings().request_content_capture_enabled,
            "dropped_content": sum(count for reason, count in counts.items() if reason != "partial"),
            "capture_diagnostics": counts}


def _write_with_retry(item) -> None:
    for attempt in range(3):
        try:
            _write_content(*item)
            return
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 255
            if code not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or attempt == 2:
                raise
            time.sleep(0.1 * (attempt + 1))


def remember_replay(api_key_id, account_id, session_key, reasoning) -> None:
    if not session_key or not reasoning:
        return
    now = time.monotonic()
    with _replay_lock:
        for call_id, text in reasoning.items():
            if len(text) > 32768 or len(call_id) > 256:
                continue
            key = (api_key_id, account_id, session_key, call_id)
            _replay[key] = (now + _REPLAY_TTL, text)
            _replay.move_to_end(key)
        while len(_replay) > _REPLAY_LIMIT:
            _replay.popitem(last=False)


def replay_for_calls(api_key_id, account_id, session_key, call_ids) -> dict[str, str]:
    if not session_key:
        return {}
    now = time.monotonic()
    result = {}
    with _replay_lock:
        while _replay and next(iter(_replay.values()))[0] <= now:
            _replay.popitem(last=False)
        for call_id in call_ids:
            entry = _replay.get((api_key_id, account_id, session_key, call_id))
            if entry is not None:
                result[call_id] = entry[1]
    return result


def _trim_live(db: sqlite3.Connection, now: int) -> None:
    cutoff = now - max(1, get_settings().request_content_retention_days) * 86400
    db.execute("DELETE FROM live_log_content WHERE updated_at < ?", (cutoff,))
    total = db.execute("SELECT coalesce(sum(payload_bytes), 0) FROM live_log_content").fetchone()[0]
    if total > _MAX_LIVE_BYTES:
        db.execute("""
            DELETE FROM live_log_content WHERE log_id IN (
                SELECT log_id FROM (
                    SELECT log_id, sum(payload_bytes) OVER (ORDER BY updated_at, log_id) - payload_bytes AS preceding
                    FROM live_log_content
                ) WHERE preceding < ?
            )
        """, (total - _MAX_LIVE_BYTES,))


def _write_content(log_id, protocol, request, response, headers, reasoning) -> None:
    if isinstance(response, bytes):
        from app.services.proxy import reconstruct_anthropic_from_sse
        from app.services.reasoning import reasoning_map_from_anthropic_content

        response = reconstruct_anthropic_from_sse(response.decode("utf-8", errors="replace"), str(request.get("model") or ""))
        reasoning = {**(reasoning or {}), **reasoning_map_from_anthropic_content(response["content"])}
    inbound = extract_request_messages(request) if isinstance(request, dict) else []
    assistant = extract_assistant_message(protocol, response)
    now = int(time.time())
    with archive_connection() as db:
        # Begin before looking up sequence numbers so multiple writers cannot allocate the same IDs.
        db.execute("BEGIN IMMEDIATE")
        detail = db.execute("SELECT * FROM live_log_content WHERE log_id = ?", (log_id,)).fetchone()
        merged = parse_reasoning_json(detail["reasoning_json"]) if detail else {}
        merged.update(reasoning or {})
        reasoning_json = dump_reasoning_json(merged)
        if reasoning_json and len(reasoning_json) > _MAX_EVENT_BYTES:
            reasoning_json = dump_reasoning_json(reasoning or {})
        headers_json = dump_headers(headers) or (detail["request_headers"] if detail else None)
        db.execute("""
            INSERT INTO live_log_content VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(log_id) DO UPDATE SET request_headers=excluded.request_headers,
                reasoning_json=excluded.reasoning_json, updated_at=excluded.updated_at
        """, (log_id, headers_json, reasoning_json, now))

        def load_messages(limit, order):
            rows = db.execute(f"SELECT seq, role, content_json FROM live_log_messages WHERE log_id = ? "
                              f"ORDER BY seq {order} LIMIT ?", (log_id, limit)).fetchall()
            return [decode_stored_message(SimpleNamespace(**row)) for row in rows]

        tail = load_messages(1, "DESC")
        last = db.execute("SELECT max(seq) FROM live_log_messages WHERE log_id = ?", (log_id,)).fetchone()[0]
        to_store = new_messages_to_store(tail, inbound, assistant, head_loader=lambda: load_messages(len(inbound), "ASC"))
        first = _LIVE_SEQ if last is None else last + 1
        db.executemany("INSERT INTO live_log_messages VALUES (?, ?, ?, ?)", (
            (log_id, first + index, str(message.get("role") or "user"), encode_message_content(message))
            for index, message in enumerate(to_store)
        ))
        db.execute("""
            DELETE FROM live_log_messages WHERE log_id = ? AND seq IN (
                SELECT seq FROM live_log_messages WHERE log_id = ? ORDER BY seq DESC LIMIT -1 OFFSET ?
            )
        """, (log_id, log_id, MAX_LOG_MESSAGES))
        size = db.execute("SELECT coalesce(sum(length(CAST(content_json AS BLOB)) + 128), 0) "
                          "FROM live_log_messages WHERE log_id = ?", (log_id,)).fetchone()[0]
        size += sum(len(value.encode()) for value in (headers_json, reasoning_json) if value) + 128
        db.execute("UPDATE live_log_content SET payload_bytes = ? WHERE log_id = ?", (size, log_id))
        _trim_live(db, now)


@contextmanager
def content_worker():
    stop = threading.Event()

    def run():
        trim_at = 0.0
        while not stop.is_set():
            try:
                item = _queue.get(timeout=0.25)
            except Empty:
                if time.monotonic() >= trim_at:
                    trim_at = time.monotonic() + 3600
                    try:
                        if archive_path().exists():
                            with archive_connection() as db:
                                _trim_live(db, int(time.time()))
                    except (OSError, ValueError, sqlite3.Error):
                        _LOG.warning("Request content retention deferred", exc_info=True)
                continue
            try:
                _write_with_retry(item)
            except Exception as exc:
                code = getattr(exc, "sqlite_errorcode", 0) & 255
                reason = {
                    sqlite3.SQLITE_BUSY: "database_busy", sqlite3.SQLITE_LOCKED: "database_busy",
                    sqlite3.SQLITE_READONLY: "database_unwritable", sqlite3.SQLITE_CANTOPEN: "database_unwritable",
                    sqlite3.SQLITE_PERM: "database_unwritable", sqlite3.SQLITE_FULL: "disk_full",
                }.get(code, "database_unwritable" if isinstance(exc, PermissionError | IsADirectoryError) else "write_failed")
                _count_capture(reason)
                _LOG.warning("Request content write failed", exc_info=True)
            finally:
                _queue.task_done()

    worker = threading.Thread(target=run, name="request-content-writer", daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=4)
        while True:
            try:
                _queue.get_nowait()
                _queue.task_done()
                _count_capture("shutdown")
            except Empty:
                break
        with _replay_lock:
            _replay.clear()


@contextmanager
def read_content(archived: bool):
    source = Path(get_settings().database_path).expanduser().resolve()
    archive = archive_path()
    use_archive = archive.is_file()
    base = archive if archived and use_archive else source
    with closing(sqlite3.connect(f"{base.as_uri()}?mode=ro", uri=True, timeout=1)) as db:
        db.row_factory = sqlite3.Row
        if base == archive:
            legacy_details, legacy_messages, live = "legacy_log_content", "legacy_log_messages", "main"
        else:
            legacy_details, legacy_messages, live = "request_logs", "request_log_messages", None
            if use_archive:
                db.execute("ATTACH DATABASE ? AS content", (f"{archive.as_uri()}?mode=ro",))
                if db.execute("SELECT 1 FROM content.sqlite_schema WHERE name = 'live_log_messages'").fetchone():
                    live = "content"
        db.execute("BEGIN")
        yield db, legacy_details, legacy_messages, live

"""On-demand, resumable copy of frozen legacy log payloads for the gateway process."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from contextlib import closing, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import get_settings
from app.services.log_content import archive_connection, archive_path

_LOG = logging.getLogger(__name__)
_BATCH_ROWS = 128
_BATCH_BYTES = 4 * 1024 * 1024
_RESERVE_BYTES = 64 * 1024 * 1024
_THROTTLE_SECONDS = 0.05
_COUNTER = {"scanning": "scanned", "copying": "copied", "verifying": "verified"}
_TABLES = (
    ("request_logs", "legacy_log_content", "log_id",
     ("request_body", "response_body", "request_headers", "reasoning_json"),
     ("request_body", "response_body", "request_headers", "reasoning_json")),
    ("request_log_messages", "legacy_log_messages", "id",
     ("log_id", "seq", "role", "content_json", "created_at"),
     ("role", "content_json", "created_at")),
)

_lock = threading.Lock()
_stop = threading.Event()
_worker: threading.Thread | None = None
_failure: tuple[Path, dict] | None = None


class _VerificationError(RuntimeError):
    pass


def _source_path() -> Path:
    database = get_settings().database_path
    if database == ":memory:":
        raise ValueError("Legacy log migration requires a file-backed main database.")
    return Path(database).expanduser().resolve()


def _idle() -> dict:
    return {
        "state": "idle", "phase": None, "elapsed_seconds": 0.0, "error": None,
        **{f"{stage}_{unit}": 0 for stage in ("total", *_COUNTER.values()) for unit in ("rows", "bytes")},
    }


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _idle()


def _save(path: Path, job: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(job, output, separators=(",", ":"))
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    if hasattr(os, "O_DIRECTORY"):
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _free_bytes(directory: Path) -> int:
    while not directory.exists() and directory != directory.parent:
        directory = directory.parent
    return shutil.disk_usage(directory).free


def _require_space(directory: Path, payload_bytes: int, rows: int) -> None:
    # Allow room for SQLite pages/indexes, the WAL, and the next atomic checkpoint.
    needed = _RESERVE_BYTES + 2 * max(payload_bytes + 128 * rows, _BATCH_BYTES)
    available = _free_bytes(directory)
    if available < needed:
        raise RuntimeError(f"Insufficient archive disk space: need {needed} bytes free, have {available}.")


def _batch(source: sqlite3.Connection, table: int, high, after, *, scan: bool) -> tuple:
    if high is None:
        return after, 0, 0, []
    name, _, _, columns, byte_columns = _TABLES[table]
    size = " + ".join(f"coalesce(length(CAST({column} AS BLOB)), 0)" for column in byte_columns)
    if table == 0:
        # Empty strings are legacy payloads too. Skip metadata-only logs (all NULL).
        present = " OR ".join(f"{column} IS NOT NULL" for column in columns)
        size = f"CASE WHEN {present} THEN {size} END"
    bounds = "id <= ?" if after is None else "id <= ? AND id > ?"
    params = (high,) if after is None else (high, after)
    ids = []
    total_bytes = 0
    last = after
    source.execute("BEGIN")
    try:
        with closing(source.execute(
            f"SELECT id, {size} FROM {name} WHERE {bounds} ORDER BY id LIMIT ?", (*params, _BATCH_ROWS)
        )) as cursor:
            for row_id, row_bytes in cursor:
                # A single oversized SQLite row must still make progress.
                if row_bytes is not None:
                    if ids and total_bytes + row_bytes > _BATCH_BYTES:
                        break
                    ids.append(row_id)
                    total_bytes += row_bytes
                last = row_id
                if total_bytes >= _BATCH_BYTES:
                    break
        rows = []
        if ids and not scan:
            placeholders = ",".join("?" for _ in ids)
            rows = source.execute(
                f"SELECT id, {', '.join(columns)} FROM {name} WHERE id IN ({placeholders}) ORDER BY id", ids
            ).fetchall()
        return last, len(ids), total_bytes, rows
    finally:
        source.rollback()


def _run(checkpoint: Path, source_path: Path, job: dict) -> None:
    global _failure
    tick = time.monotonic()

    def advance(**changes) -> None:
        nonlocal job, tick
        now = time.monotonic()
        delta = now - tick
        updated = {
            **job, "elapsed_seconds": job["elapsed_seconds"] + delta,
            "phase_seconds": job["phase_seconds"] + delta, **changes,
        }
        _save(checkpoint, updated)
        job, tick = updated, now

    try:
        with closing(sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True, timeout=1)) as source:
            if job["high_water"] is None:
                # Read both bounds from one short SQLite snapshot.
                high = source.execute(
                    "SELECT (SELECT max(id) FROM request_logs), (SELECT max(id) FROM request_log_messages)"
                ).fetchone()
                advance(high_water=high)

            preflight = True
            while True:
                if _stop.is_set():
                    advance(state="paused")
                    return
                phase = job["phase"]
                table = job["table"]
                if table == len(_TABLES):
                    if phase == "scanning":
                        advance(state="copying", phase="copying", phase_seconds=0.0, table=0, after=None,
                                total_rows=job["scanned_rows"], total_bytes=job["scanned_bytes"])
                    elif phase == "copying":
                        if (job["copied_rows"], job["copied_bytes"]) != (job["total_rows"], job["total_bytes"]):
                            raise _VerificationError("Legacy source totals changed during copy.")
                        advance(state="verifying", phase="verifying", phase_seconds=0.0, table=0, after=None)
                    else:
                        if (job["verified_rows"], job["verified_bytes"]) != (job["total_rows"], job["total_bytes"]):
                            raise _VerificationError("Legacy source totals changed during verification.")
                        advance(state="completed", phase=None)
                        return
                    continue

                if phase == "copying" and preflight:
                    if job["copied_rows"] < job["total_rows"]:
                        _require_space(checkpoint.parent, job["total_bytes"] - job["copied_bytes"],
                                       job["total_rows"] - job["copied_rows"])
                    preflight = False
                last, count, size, rows = _batch(source, table, job["high_water"][table], job["after"],
                                                  scan=phase == "scanning")
                if last == job["after"]:
                    advance(table=table + 1, after=None)
                    continue

                if rows:
                    _, destination, key, columns, _ = _TABLES[table]
                    if phase == "copying":
                        _require_space(checkpoint.parent, size, count)
                        placeholders = ",".join("?" for _ in (key, *columns))
                        updates = ", ".join(f"{column}=excluded.{column}" for column in columns)
                        with archive_connection() as archive:
                            # The sidecar may survive power loss; the archive commit must too.
                            archive.execute("PRAGMA synchronous=FULL")
                            archive.executemany(
                                f"INSERT INTO {destination} ({key}, {', '.join(columns)}) VALUES ({placeholders}) "
                                f"ON CONFLICT({key}) DO UPDATE SET {updates}", rows,
                            )
                    else:
                        placeholders = ",".join("?" for _ in rows)
                        with archive_connection() as archive:
                            archived = archive.execute(
                                f"SELECT {key}, {', '.join(columns)} FROM {destination} "
                                f"WHERE {key} IN ({placeholders}) ORDER BY {key}", tuple(row[0] for row in rows),
                            ).fetchall()
                        if rows != [tuple(row) for row in archived]:
                            raise _VerificationError(f"Archive verification failed in {destination} through ID {last}.")

                counter = _COUNTER[phase]
                advance(after=last, **{f"{counter}_rows": job[f"{counter}_rows"] + count,
                                       f"{counter}_bytes": job[f"{counter}_bytes"] + size})
                _stop.wait(_THROTTLE_SECONDS)
    except Exception as exc:
        _LOG.exception("Legacy log migration failed")
        changes = {"state": "failed", "error": str(exc), "retry_copy": isinstance(exc, _VerificationError)}
        try:
            advance(**changes)
        except OSError:
            # Keep the previous durable cursor; a full filesystem may also prevent saving an error.
            with _lock:
                _failure = (checkpoint, {**job, **changes})


def _checkpoint() -> Path:
    archive = Path(archive_path()).expanduser().resolve()
    return archive.with_name(archive.name + ".migration.json")


def _status(checkpoint: Path) -> dict:
    try:
        job = _failure[1] if _failure is not None and _failure[0] == checkpoint else _load(checkpoint)
    except (OSError, ValueError) as exc:
        job = {**_idle(), "state": "failed", "error": f"Cannot read the migration checkpoint: {exc}"}
    result = {key: job[key] for key in _idle()}
    phase = job["phase"]
    if job["state"] in _COUNTER and (_worker is None or not _worker.is_alive()):
        result["state"] = "paused"
    rate = remaining = finish = None
    if result["state"] in _COUNTER and job["phase_seconds"] > 0:
        counter = _COUNTER[phase]
        rate = job[f"{counter}_bytes"] / job["phase_seconds"] or None
        if phase != "scanning":
            unit = "bytes" if rate is not None else "rows"
            done, total = job[f"{counter}_{unit}"], job[f"total_{unit}"]
            if done:
                pending = max(total - done, 0) + (total if phase == "copying" else 0)
                remaining = pending * job["phase_seconds"] / done
                if remaining < 10**10:
                    finish = (datetime.now(UTC) + timedelta(seconds=remaining)).isoformat()
    result.update(bytes_per_second=rate, remaining_seconds=remaining, estimated_finish_at=finish)
    with suppress(OSError):
        result["free_bytes"] = _free_bytes(checkpoint.parent)
    return result


def legacy_content_archived() -> bool:
    """Check the durable cutover marker without computing progress or touching SQLite."""
    try:
        return _load(_checkpoint())["state"] == "completed"
    except (OSError, ValueError):
        return False


def migration_status() -> dict:
    """Return progress without acquiring a SQLite lock or starting work."""
    with _lock:
        return _status(_checkpoint())


def migration_start() -> dict:
    """Start or resume the job. Repeated calls during work or after completion are idempotent."""
    global _worker, _failure
    with _lock:
        checkpoint = _checkpoint()
        if _worker is not None and _worker.is_alive():
            return _status(checkpoint)
        job = _idle()
        try:
            job = _load(checkpoint)
            source = _source_path()
            if job.get("source", str(source)) != str(source):
                raise ValueError("Migration checkpoint belongs to a different main database.")
            if job["state"] == "completed":
                return _status(checkpoint)
            if job["phase"] is None:
                job.update(source=str(source), phase="scanning", phase_seconds=0.0, high_water=None,
                           table=0, after=None)
            elif job.get("retry_copy"):
                # Repair and re-verify the same scope against the original scan totals.
                job.update(phase="copying", phase_seconds=0.0, table=0, after=None,
                           copied_rows=0, copied_bytes=0, verified_rows=0, verified_bytes=0)
            job.update(state=job["phase"], error=None, retry_copy=False)
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            _save(checkpoint, job)
            _failure = None
            _stop.clear()
            _worker = threading.Thread(target=_run, args=(checkpoint, source, job), name="legacy-log-migration",
                                       daemon=True)
            _worker.start()
        except (OSError, ValueError, RuntimeError) as exc:
            _failure = (checkpoint, {**job, "state": "failed", "error": str(exc)})
        return _status(checkpoint)


def migration_pause() -> dict:
    """Request a pause; the returned state stays active until the in-flight batch ends."""
    with _lock:
        _stop.set()
        return _status(_checkpoint())


def migration_shutdown() -> None:
    """Request a checkpointed stop during application shutdown."""
    with _lock:
        _stop.set()
        worker = _worker
    if worker is not None:
        worker.join(timeout=5)

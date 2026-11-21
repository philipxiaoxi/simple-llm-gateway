"""Remove verified legacy copies with a checkpoint in the same main DB transaction."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.log_content import archive_path
from app.services.log_content_migration import _TABLES, _batch, _checkpoint, _load, _require_space, _source_path

_LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()
_PAUSE = threading.Event()
_worker: threading.Thread | None = None
_failure: tuple[Path, str] | None = None


def _source_and_migration():
    source = _source_path()
    migration = _load(_checkpoint())
    if (
        migration["state"] != "completed" or migration.get("source") != str(source)
        or len(migration.get("high_water") or []) != len(_TABLES)
        or migration["verified_rows"] != migration["total_rows"]
        or migration["verified_bytes"] != migration["total_bytes"]
        or not archive_path().is_file()
    ):
        return source, None
    return source, migration


def _initial(migration):
    return {
        "state": "idle" if migration else "unavailable", "table": 0, "after": None,
        "cleaned_rows": 0, "cleaned_bytes": 0, "elapsed_seconds": 0.0,
        "total_rows": migration["total_rows"] if migration else 0,
        "total_bytes": migration["total_bytes"] if migration else 0,
        "reusable_bytes": None, "error": None,
    }


def _progress(db, migration):
    table_exists = db.execute("SELECT 1 FROM sqlite_schema WHERE name = 'legacy_log_cleanup'").fetchone()
    row = db.execute("SELECT job FROM legacy_log_cleanup WHERE id = 1").fetchone() if table_exists else None
    job = json.loads(row[0]) if row else _initial(migration)
    if job.get("scope", migration["high_water"]) != migration["high_water"] or any(
        job[f"total_{unit}"] != migration[f"total_{unit}"] for unit in ("rows", "bytes")
    ):
        raise ValueError("Cleanup checkpoint does not match the verified archive scope.")
    return job


def _status() -> dict:
    source, migration = _source_and_migration()
    job = _initial(migration)
    if migration:
        with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=1)) as db:
            job = _progress(db, migration)
        if _failure and _failure[0] == source:
            job.update(state="failed", error=_failure[1])
        elif _worker is None or not _worker.is_alive():
            if job["state"] == "cleaning":
                job["state"] = "paused"
        elif job["state"] in {"idle", "paused"}:
            job["state"] = "cleaning"
    remaining = finish = None
    unit = "bytes" if job["total_bytes"] else "rows"
    done, total = job[f"cleaned_{unit}"], job[f"total_{unit}"]
    if job["state"] == "cleaning" and done and job["elapsed_seconds"]:
        remaining = min(10**9, max(0, total - done) * job["elapsed_seconds"] / done)
        finish = (datetime.now(UTC) + timedelta(seconds=remaining)).isoformat()
    return {**{k: v for k, v in job.items() if k not in {"table", "after", "scope"}},
            "remaining_seconds": remaining, "estimated_finish_at": finish}


def _run(source, archive, migration, job) -> None:
    global _failure
    tick = time.monotonic()
    try:
        with (closing(sqlite3.connect(f"{source.as_uri()}?mode=rw", uri=True, timeout=1)) as db,
              closing(sqlite3.connect(f"{archive.as_uri()}?mode=ro", uri=True, timeout=1)) as archived):
            db.execute("PRAGMA synchronous=FULL")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]

            def checkpoint(**changes):
                nonlocal job, tick
                now = time.monotonic()
                updated = {**job, "elapsed_seconds": job["elapsed_seconds"] + now - tick, **changes}
                db.execute("INSERT OR REPLACE INTO legacy_log_cleanup VALUES (1, ?)", (json.dumps(updated),))
                db.commit()
                job, tick = updated, now

            db.execute("CREATE TABLE IF NOT EXISTS legacy_log_cleanup (id INTEGER PRIMARY KEY, job TEXT NOT NULL)")
            checkpoint(state="cleaning", scope=migration["high_water"], error=None)
            while job["table"] < len(_TABLES):
                if _PAUSE.is_set():
                    checkpoint(state="paused")
                    return
                table, after = job["table"], job["after"]
                last, count, size, rows = _batch(db, table, migration["high_water"][table], after, scan=False)
                if last == after:
                    checkpoint(table=table + 1, after=None)
                    continue
                if rows:
                    name, destination, key, columns, _ = _TABLES[table]
                    ids = [row[0] for row in rows]
                    placeholders = ",".join("?" for _ in ids)
                    lookup = f"({placeholders})"
                    fields = ", ".join(columns)
                    if rows != archived.execute(
                        f"SELECT {key}, {fields} FROM {destination} WHERE {key} IN {lookup} ORDER BY {key}", ids,
                    ).fetchall():
                        raise ValueError(f"Archive contents differ from the main database in {destination} through ID {last}.")
                    _require_space(source.parent, size, count)
                    db.execute("BEGIN IMMEDIATE")
                    if rows != db.execute(
                        f"SELECT id, {fields} FROM {name} WHERE id IN {lookup} ORDER BY id", ids,
                    ).fetchall():
                        raise ValueError("Main database contents changed before cleanup; the batch was preserved.")
                    if table == 0:
                        assignments = ", ".join(f"{column} = NULL" for column in columns)
                        db.execute(f"UPDATE {name} SET {assignments} WHERE id IN {lookup}", ids)
                    else:
                        db.execute(f"DELETE FROM {name} WHERE id IN {lookup}", ids)
                checkpoint(after=last, cleaned_rows=job["cleaned_rows"] + count,
                           cleaned_bytes=job["cleaned_bytes"] + size)
                _PAUSE.wait(0.1)

            if any(job[f"cleaned_{unit}"] != job[f"total_{unit}"] for unit in ("bytes", "rows")):
                raise ValueError("Cleanup totals differ from the verified archive scope.")
            checkpoint(state="completed", reusable_bytes=db.execute("PRAGMA freelist_count").fetchone()[0] * page_size)
    except Exception as exc:
        _LOG.exception("Legacy request content cleanup stopped")
        _failure = (source, str(exc))


def cleanup_status() -> dict:
    with _LOCK:
        return _status()


def cleanup_start() -> dict:
    global _worker, _failure
    with _LOCK:
        source, migration = _source_and_migration()
        if migration is None:
            raise ValueError("A verified and readable archive is required before cleanup.")
        if _worker is not None and _worker.is_alive():
            return _status()
        with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=1)) as db:
            job = _progress(db, migration)
        if job["state"] == "completed":
            return _status()
        _PAUSE.clear()
        _failure = None
        _worker = threading.Thread(target=_run, args=(source, archive_path(), migration, job), daemon=True,
                                   name="legacy-log-cleanup")
        _worker.start()
        return _status()


def cleanup_pause() -> dict:
    with _LOCK:
        _PAUSE.set()
        return _status()


def cleanup_shutdown() -> None:
    with _LOCK:
        _PAUSE.set()
        worker = _worker
    if worker:
        worker.join(timeout=5)

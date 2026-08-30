from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import get_settings

DEFAULTS: dict[str, dict[str, int]] = {
    "catalog": {"interval_seconds": 172800},
    "quota": {"interval_seconds": 3600},
    "oauth": {"interval_seconds": 600, "soon_seconds": 1200},
    "leaderboard": {"interval_seconds": 43200},
}


def _defaults() -> dict[str, dict[str, int]]:
    settings = get_settings()
    return {
        "catalog": {"interval_seconds": 172800},
        "quota": {"interval_seconds": max(60, settings.quota_refresh_interval_seconds)},
        "oauth": {"interval_seconds": 600, "soon_seconds": 1200},
        "leaderboard": {
            "interval_seconds": max(60, settings.aihot_leaderboard_ttl_seconds),
        },
    }


ALLOWED_KEYS = {job_id: set(params) for job_id, params in DEFAULTS.items()}

_CACHE: dict[str, dict[str, int]] | None = None


def job_settings_path() -> Path:
    settings = get_settings()
    if settings.database_path == ":memory:":
        return Path("data") / "job_settings.json"
    return Path(settings.database_path).expanduser().resolve().parent / "job_settings.json"


def reset_job_settings() -> None:
    global _CACHE
    _CACHE = None


def get_all_job_params() -> dict[str, dict[str, int]]:
    global _CACHE
    if _CACHE is None:
        _CACHE = _load()
    return {job_id: dict(values) for job_id, values in _CACHE.items()}


def get_job_params(job_id: str) -> dict[str, int]:
    return dict(get_all_job_params().get(job_id) or DEFAULTS.get(job_id) or {})


def get_job_int(job_id: str, key: str, default: int) -> int:
    value = get_job_params(job_id).get(key, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def update_job_params(job_id: str, payload: dict[str, Any]) -> dict[str, int]:
    if job_id not in DEFAULTS:
        raise ValueError("任务不存在")
    allowed = ALLOWED_KEYS[job_id]
    current = get_job_params(job_id)
    changed = False
    for key, raw in payload.items():
        if key not in allowed or raw is None:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{key} 必须是整数") from error
        if value < 0:
            raise ValueError(f"{key} 必须大于或等于 0")
        current[key] = value
        changed = True
    if changed:
        all_params = get_all_job_params()
        all_params[job_id] = current
        _store(all_params)
    return get_job_params(job_id)


def _merged(raw: dict[str, Any] | None) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {job_id: dict(values) for job_id, values in _defaults().items()}
    if not isinstance(raw, dict):
        return result
    for job_id, defaults in DEFAULTS.items():
        incoming = raw.get(job_id)
        if not isinstance(incoming, dict):
            continue
        if job_id == "leaderboard" and incoming.get("interval_seconds") is None and incoming.get("ttl_seconds") is not None:
            incoming = {**incoming, "interval_seconds": incoming["ttl_seconds"]}
        for key in defaults:
            value = incoming.get(key)
            if value is None:
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed >= 0:
                result[job_id][key] = parsed
    return result


def _load() -> dict[str, dict[str, int]]:
    path = job_settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = None
    return _merged(raw if isinstance(raw, dict) else None)


def _store(params: dict[str, dict[str, int]]) -> None:
    global _CACHE
    _CACHE = {job_id: dict(values) for job_id, values in params.items()}
    path = job_settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_CACHE, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return

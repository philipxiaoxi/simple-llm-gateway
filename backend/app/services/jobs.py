from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.clock import utcnow
from app.config import get_settings
from app.db import get_session_factory
from app.services import content_audit, grok_oauth, model_caps, quota
from app.services import leaderboard as leaderboard_service
from app.services.job_settings import get_job_int, get_job_params, update_job_params


class JobBusyError(RuntimeError):
    pass


JOB_CATALOG = "catalog"
JOB_QUOTA = "quota"
JOB_OAUTH = "oauth"
JOB_LEADERBOARD = "leaderboard"
JOB_CONTENT_AUDIT = "content_audit"
LOOP_JOBS = (JOB_CATALOG, JOB_QUOTA, JOB_OAUTH, JOB_LEADERBOARD)
ALL_JOBS = LOOP_JOBS + (JOB_CONTENT_AUDIT,)

PARAM_LIMITS: dict[str, tuple[int, int]] = {
    "interval_seconds": (60, 604800),
    "ttl_seconds": (60, 604800),
    "min_refresh_seconds": (0, 86400),
    "soon_seconds": (60, 86400),
}

PARAM_LABELS = {
    "interval_seconds": "刷新间隔（秒）",
    "ttl_seconds": "缓存有效期（秒）",
    "min_refresh_seconds": "最短刷新间隔（秒）",
    "soon_seconds": "提前刷新（秒）",
}

JOB_META = {
    JOB_CATALOG: {
        "name": "模型目录缓存",
        "description": "从 models.dev 拉取模型上下文、输出上限和模态，供获取模型与榜单补全使用。",
        "kind": "loop",
        "source_url": model_caps.MODELS_DEV_URL,
    },
    JOB_QUOTA: {
        "name": "上游额度刷新",
        "description": "按间隔扫描启用中的上游账号，拉取余额或用量。",
        "kind": "loop",
        "source_url": None,
    },
    JOB_OAUTH: {
        "name": "Grok OAuth 刷新",
        "description": "在 access token 即将过期时，用 refresh token 换新。",
        "kind": "loop",
        "source_url": None,
    },
    JOB_LEADERBOARD: {
        "name": "模型榜缓存",
        "description": "按间隔从 AIHOT 拉取总榜。管理端和公开页只读缓存，立即请求可强制拉取。",
        "kind": "loop",
        "source_url": None,
    },
    JOB_CONTENT_AUDIT: {
        "name": "内容审计扫描",
        "description": "手动扫描请求正文，产出敏感词 / PII / 密钥命中",
        "kind": "manual",
        "source_url": None,
    },
}


@dataclass
class JobRuntime:
    running: bool = False
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_ok: bool | None = None
    last_message: str | None = None
    error_message: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


_runtime: dict[str, JobRuntime] = {job_id: JobRuntime() for job_id in ALL_JOBS}
_locks: dict[str, asyncio.Lock] = {}
_wake_events: dict[str, asyncio.Event] = {}
_wake_reasons: dict[str, str] = {}
_loop_tasks: list[asyncio.Task] = []


def reset_jobs() -> None:
    global _runtime, _locks, _wake_events, _wake_reasons, _loop_tasks
    _runtime = {job_id: JobRuntime() for job_id in ALL_JOBS}
    _locks = {}
    _wake_events = {}
    _wake_reasons = {}
    _loop_tasks = []


def get_job_runtime(job_id: str) -> JobRuntime:
    return _runtime[job_id]


def _lock(job_id: str) -> asyncio.Lock:
    lock = _locks.get(job_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[job_id] = lock
    return lock


def _event(job_id: str) -> asyncio.Event:
    event = _wake_events.get(job_id)
    if event is None:
        event = asyncio.Event()
        _wake_events[job_id] = event
    return event


def humanize_seconds(seconds: int) -> str:
    value = max(0, int(seconds))
    if value < 60:
        return f"{value} 秒"
    if value % 86400 == 0:
        days = value // 86400
        return f"{days} 天"
    if value % 3600 == 0:
        hours = value // 3600
        return f"{hours} 小时"
    if value % 60 == 0:
        minutes = value // 60
        return f"{minutes} 分钟"
    if value >= 86400:
        days = value / 86400
        return f"{days:.1f} 天"
    if value >= 3600:
        hours = value / 3600
        return f"{hours:.1f} 小时"
    minutes = value / 60
    return f"{minutes:.1f} 分钟"


def _validate_param(key: str, value: int) -> int:
    bounds = PARAM_LIMITS.get(key)
    if bounds is None:
        raise ValueError(f"不支持的参数 {key}")
    low, high = bounds
    if value < low or value > high:
        raise ValueError(f"{PARAM_LABELS.get(key, key)} 需要在 {low} 到 {high} 之间")
    return value


def apply_job_params(job_id: str, payload: dict[str, Any]) -> dict[str, int]:
    if job_id not in JOB_META:
        raise ValueError("任务不存在")
    allowed = get_job_params(job_id)
    cleaned: dict[str, Any] = {}
    for key, raw in payload.items():
        if key not in allowed or raw is None:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{PARAM_LABELS.get(key, key)} 必须是整数") from error
        cleaned[key] = _validate_param(key, value)
    updated = update_job_params(job_id, cleaned)
    if job_id in LOOP_JOBS:
        _wake(job_id, "reload")
    return updated


def _wake(job_id: str, reason: str) -> None:
    _wake_reasons[job_id] = reason
    _event(job_id).set()


def request_run(job_id: str) -> None:
    _wake(job_id, "run")


def reset_loop_wait(job_id: str) -> None:
    if job_id in LOOP_JOBS:
        _wake(job_id, "reload")


async def _wait_next(job_id: str) -> str:
    timeout = max(60, get_job_int(job_id, "interval_seconds", 3600))
    event = _event(job_id)
    event.clear()
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        return _wake_reasons.pop(job_id, "run")
    except TimeoutError:
        return "interval"


async def _execute(job_id: str) -> dict[str, Any]:
    if job_id == JOB_CATALOG:
        before = model_caps.catalog_cache_info().get("fetched_at")
        await model_caps.refresh_catalog_index(force=True)
        info = model_caps.catalog_cache_info()
        count = int(info.get("model_count") or 0)
        fetched_at = info.get("fetched_at")
        if info.get("ok") and fetched_at is not None and fetched_at != before:
            return {"message": f"已缓存 {count} 个模型", "model_count": count}
        if info.get("ok"):
            raise RuntimeError(f"拉取 models.dev 失败，沿用缓存 {count} 个模型")
        raise RuntimeError("拉取 models.dev 失败")
    if job_id == JOB_QUOTA:
        refreshed = await quota.refresh_due_quotas()
        return {"message": f"已刷新 {refreshed} 个账号额度", "refreshed": refreshed}
    if job_id == JOB_OAUTH:
        refreshed = await grok_oauth.refresh_expiring_oauth_tokens()
        return {"message": f"已刷新 {refreshed} 个 Grok Token", "refreshed": refreshed}
    if job_id == JOB_LEADERBOARD:
        session = get_session_factory()()
        try:
            payload = await leaderboard_service.get_leaderboard(session, force=True, ignore_cooldown=True)
        finally:
            session.close()
        total = int(payload.get("total") or 0)
        error_message = payload.get("error_message")
        if error_message and not payload.get("items"):
            raise RuntimeError(str(error_message))
        message = f"已缓存 {total} 条榜单"
        if error_message:
            message = f"{error_message}（{message}）"
        return {"message": message, "total": total, "error_message": error_message}
    if job_id == JOB_CONTENT_AUDIT:
        return content_audit.start_scan()
    raise ValueError("任务不存在")


async def run_job(job_id: str) -> dict[str, Any]:
    if job_id not in JOB_META:
        raise ValueError("任务不存在")
    lock = _lock(job_id)
    if lock.locked():
        raise JobBusyError("任务正在运行")
    async with lock:
        state = _runtime[job_id]
        state.running = True
        state.last_started_at = utcnow()
        state.error_message = None
        try:
            extra = await _execute(job_id)
            state.last_ok = True
            state.last_message = str(extra.get("message") or "已完成")
            state.error_message = extra.get("error_message") if extra.get("lexicon_ok") is False else None
            state.extra = extra
            return extra
        except Exception as error:
            state.last_ok = False
            state.last_message = None
            state.error_message = str(error)
            raise
        finally:
            state.running = False
            state.last_finished_at = utcnow()


async def run_job_loop(job_id: str) -> None:
    # 启动后先等间隔，避免发版立刻四任务同时跑；手动唤醒（reason=run）仍立即执行。
    while True:
        while True:
            reason = await _wait_next(job_id)
            if reason != "reload":
                break
        try:
            await run_job(job_id)
        except Exception:
            pass


def start_job_loops() -> list[asyncio.Task]:
    global _loop_tasks
    tasks = [asyncio.create_task(run_job_loop(job_id)) for job_id in LOOP_JOBS]
    _loop_tasks = tasks
    return tasks


def _expires_at(fetched_at: datetime | None, ttl_seconds: int) -> datetime | None:
    if fetched_at is None:
        return None
    return fetched_at + timedelta(seconds=max(0, ttl_seconds))


def _next_run_at(job_id: str, state: JobRuntime, interval: int) -> datetime | None:
    if JOB_META[job_id]["kind"] != "loop":
        return None
    if state.running:
        return None
    if state.last_finished_at is None:
        return utcnow() + timedelta(seconds=interval)
    return state.last_finished_at + timedelta(seconds=interval)


def _param_specs(job_id: str) -> list[dict[str, Any]]:
    if JOB_META[job_id]["kind"] != "loop":
        return []
    params = get_job_params(job_id)
    items: list[dict[str, Any]] = []
    for key, value in params.items():
        low, high = PARAM_LIMITS[key]
        items.append(
            {
                "key": key,
                "label": PARAM_LABELS[key],
                "value": value,
                "min": low,
                "max": high,
                "hint": humanize_seconds(value),
            }
        )
    return items


def _catalog_snapshot() -> dict[str, Any]:
    info = model_caps.catalog_cache_info()
    interval = get_job_int(JOB_CATALOG, "interval_seconds", 172800)
    fetched_at = info.get("fetched_at")
    return {
        "cache_fetched_at": fetched_at,
        "cache_expires_at": _expires_at(fetched_at, interval),
        "cache_ok": bool(info.get("ok")),
        "model_count": info.get("model_count") or 0,
        "ttl_seconds": interval,
    }


def _quota_snapshot() -> dict[str, Any]:
    interval = get_job_int(JOB_QUOTA, "interval_seconds", 3600)
    session = get_session_factory()()
    try:
        due = quota.accounts_due_for_quota_refresh(session)
        rows = quota.quota_account_stats(session)
    finally:
        session.close()
    return {
        "due_count": len(due),
        "account_count": rows["account_count"],
        "oldest_quota_at": rows["oldest_quota_at"],
        "newest_quota_at": rows["newest_quota_at"],
        "ttl_seconds": interval,
        "cache_fetched_at": rows["newest_quota_at"],
        "cache_expires_at": _expires_at(rows["newest_quota_at"], interval) if rows["newest_quota_at"] else None,
    }


def _oauth_snapshot() -> dict[str, Any]:
    interval = get_job_int(JOB_OAUTH, "interval_seconds", 600)
    soon = get_job_int(JOB_OAUTH, "soon_seconds", 1200)
    session = get_session_factory()()
    try:
        stats = grok_oauth.oauth_token_stats(session, soon_seconds=soon)
    finally:
        session.close()
    return {
        "due_count": stats["due_count"],
        "token_count": stats["token_count"],
        "earliest_expires_at": stats["earliest_expires_at"],
        "ttl_seconds": interval,
        "cache_fetched_at": stats["latest_updated_at"],
        "cache_expires_at": stats["earliest_expires_at"],
    }


def _content_audit_snapshot() -> dict[str, Any]:
    interval = get_job_int(JOB_CONTENT_AUDIT, "interval_seconds", 86400)
    stats = content_audit.progress_stats()
    state = _runtime[JOB_CONTENT_AUDIT]
    extra = state.extra or {}
    return {
        "cache_fetched_at": state.last_finished_at,
        "cache_expires_at": _expires_at(state.last_finished_at, interval) if state.last_finished_at else None,
        "cache_ok": bool(state.last_ok),
        "ttl_seconds": interval,
        "processed": extra.get("processed"),
        "new_findings": extra.get("new_findings"),
        "remaining": stats["remaining"],
        "lexicon_ok": extra.get("lexicon_ok"),
        "scanned_count": stats["scanned_count"],
        "total_logs": stats["total_logs"],
        "finding_count": stats["finding_count"],
    }


def _leaderboard_snapshot() -> dict[str, Any]:
    ttl = get_job_int(JOB_LEADERBOARD, "interval_seconds", get_settings().aihot_leaderboard_ttl_seconds)
    session = get_session_factory()()
    try:
        snapshot = leaderboard_service.latest_snapshot(session)
    finally:
        session.close()
    fetched_at = snapshot.fetched_at if snapshot else None
    total = 0
    if snapshot and snapshot.entries_json:
        try:
            loaded = json.loads(snapshot.entries_json)
            if isinstance(loaded, list):
                total = len(loaded)
        except Exception:
            total = 0
    stale = bool(snapshot) and not leaderboard_service.cache_is_fresh(snapshot)
    return {
        "cache_fetched_at": fetched_at,
        "cache_expires_at": _expires_at(fetched_at, ttl),
        "cache_ok": bool(snapshot and snapshot.entries_json and snapshot.entries_json != "[]"),
        "stale": stale,
        "total": total,
        "ttl_seconds": ttl,
        "source_url": get_settings().aihot_leaderboard_url,
        "error_message": snapshot.error_message if snapshot else None,
    }


_SNAPSHOTTERS = {
    JOB_CATALOG: _catalog_snapshot,
    JOB_QUOTA: _quota_snapshot,
    JOB_OAUTH: _oauth_snapshot,
    JOB_LEADERBOARD: _leaderboard_snapshot,
    JOB_CONTENT_AUDIT: _content_audit_snapshot,
}


def list_jobs() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for job_id in ALL_JOBS:
        meta = JOB_META[job_id]
        state = _runtime[job_id]
        params = get_job_params(job_id)
        interval = int(params.get("interval_seconds") or params.get("ttl_seconds") or 0)
        snapshot = _SNAPSHOTTERS[job_id]()
        source_url = snapshot.get("source_url") or meta.get("source_url")
        error_message = state.error_message or snapshot.get("error_message")
        running = state.running
        if job_id == JOB_CONTENT_AUDIT:
            running = content_audit.scan_status()["running"]
        items.append(
            {
                "id": job_id,
                "name": meta["name"],
                "description": meta["description"],
                "kind": meta["kind"],
                "source_url": source_url,
                "running": running,
                "last_started_at": state.last_started_at,
                "last_finished_at": state.last_finished_at,
                "last_ok": state.last_ok,
                "last_message": state.last_message,
                "error_message": error_message,
                "next_run_at": _next_run_at(job_id, state, interval),
                "cache_fetched_at": snapshot.get("cache_fetched_at"),
                "cache_expires_at": snapshot.get("cache_expires_at"),
                "cache_ok": snapshot.get("cache_ok"),
                "ttl_seconds": snapshot.get("ttl_seconds") or interval,
                "params": _param_specs(job_id),
                "details": {
                    key: (value.isoformat() if isinstance(value, datetime) else value)
                    for key, value in snapshot.items()
                    if key
                    not in {
                        "cache_fetched_at",
                        "cache_expires_at",
                        "cache_ok",
                        "ttl_seconds",
                        "source_url",
                        "error_message",
                    }
                },
            }
        )
    return items

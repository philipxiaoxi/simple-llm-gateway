"""管理端读缓存实例。

定时任务面板要扫库做每个任务的快照，这里放一个进程内 TTL 缓存。
概览图表不再走这份缓存，改由后台任务写入 `dashboard_stats_snapshots`。
"""

from __future__ import annotations

from app.cache import TTLCache
from app.config import get_settings


def jobs_cache_seconds() -> float:
    return float(get_settings().admin_jobs_cache_seconds)


jobs_cache: TTLCache = TTLCache("admin-jobs", ttl=jobs_cache_seconds, max_entries=8)


def reset_read_caches() -> None:
    jobs_cache.clear()


def invalidate_job_snapshot(job_id: str) -> None:
    jobs_cache.delete(job_id)


def invalidate_jobs_snapshots() -> None:
    """任务面板「刷新状态」：丢弃快照缓存，下一次读取全部重算。"""
    jobs_cache.clear()

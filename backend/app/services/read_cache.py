"""管理端读缓存实例。

统计聚合与定时任务面板都要扫 request_logs / request_log_messages 这类大表，
生产数据量上来后一次要几百毫秒甚至秒级。这里统一放几个进程内 TTL 缓存：

- `stats_cache`：概览页图表聚合（/api/admin/dashboard/stats）
- `jobs_cache`：定时任务面板里每个任务的 DB 快照（/api/admin/jobs）
- `progress_cache`：内容审计进度（/api/admin/jobs 与 /api/admin/content-audit/summary 共用）

语义统一为「请求到来时惰性刷新」：过期后由下一个请求重算，不预先跑后台任务。
手动执行任务、修改任务参数、扫描开始/结束时会主动失效对应条目，避免看到旧状态。
"""

from __future__ import annotations

from app.cache import TTLCache
from app.config import get_settings

CONTENT_AUDIT_JOB_KEY = "content_audit"


def stats_cache_seconds() -> float:
    return float(get_settings().admin_stats_cache_seconds)


def jobs_cache_seconds() -> float:
    return float(get_settings().admin_jobs_cache_seconds)


# 统计接口按 days(1~90) × 上海自然日分 key，留够槽位避免来回切区间时互相挤掉
stats_cache: TTLCache = TTLCache("admin-stats", ttl=stats_cache_seconds, max_entries=32)
jobs_cache: TTLCache = TTLCache("admin-jobs", ttl=jobs_cache_seconds, max_entries=8)
progress_cache: TTLCache = TTLCache("content-audit-progress", ttl=jobs_cache_seconds, max_entries=4)


def reset_read_caches() -> None:
    stats_cache.clear()
    jobs_cache.clear()
    progress_cache.clear()


def invalidate_stats_cache() -> None:
    stats_cache.clear()


def invalidate_job_snapshot(job_id: str) -> None:
    jobs_cache.delete(job_id)


def invalidate_jobs_snapshots() -> None:
    """任务面板「刷新状态」：连同内容审计进度一起丢弃，下一次读取全部重算。"""
    jobs_cache.clear()
    progress_cache.clear()


def invalidate_content_audit_progress() -> None:
    """扫描进度/命中数变了：失效进度缓存，并让定时任务面板下次重算内容审计快照。"""
    progress_cache.clear()
    jobs_cache.delete(CONTENT_AUDIT_JOB_KEY)

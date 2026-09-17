from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import delete, select

from app.clock import utcnow
from app.config import get_settings
from app.db import get_session_factory
from app.models import KnowledgeIngestJob, McpCallLog
from app.services import knowledge_jobs

CLEANUP_INTERVAL_SECONDS = 24 * 3600


def cleanup_once() -> dict[str, int]:
    settings = get_settings()
    job_days = max(0, int(settings.mcp_knowledge_job_retention_days))
    call_days = max(0, int(settings.mcp_call_log_retention_days))
    now = utcnow()
    removed_jobs = 0
    removed_calls = 0
    orphan_files = 0

    session = get_session_factory()()
    try:
        if job_days:
            cutoff = now - timedelta(days=job_days)
            stale_jobs = session.scalars(
                select(KnowledgeIngestJob).where(
                    KnowledgeIngestJob.status.in_(("succeeded", "failed", "canceled")),
                    KnowledgeIngestJob.finished_at.is_not(None),
                    KnowledgeIngestJob.finished_at < cutoff,
                )
            ).all()
            for job in stale_jobs:
                knowledge_jobs.delete_source(job.id)
                session.delete(job)
                removed_jobs += 1
        if call_days:
            cutoff = now - timedelta(days=call_days)
            result = session.execute(delete(McpCallLog).where(McpCallLog.created_at < cutoff))
            removed_calls = int(result.rowcount or 0)

        # 清理没有对应任务记录的原文文件
        active_ids = set(session.scalars(select(KnowledgeIngestJob.id)).all())
        for path in settings.resolved_knowledge_jobs_path.glob("*.txt"):
            try:
                job_id = int(path.stem)
            except ValueError:
                continue
            if job_id not in active_ids:
                knowledge_jobs.delete_source(job_id)
                orphan_files += 1
        session.commit()
    finally:
        session.close()
    return {"jobs": removed_jobs, "call_logs": removed_calls, "orphan_files": orphan_files}


async def retention_loop() -> None:
    """每天清理一次已结束任务、调用日志与残留原文。"""
    while True:
        try:
            await asyncio.to_thread(cleanup_once)
        except Exception as error:  # noqa: BLE001
            print(f"[knowledge-retention] cleanup failed: {error}")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)

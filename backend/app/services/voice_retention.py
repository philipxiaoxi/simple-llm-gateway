"""语音日志清理。

段落与事件是语音输入的主要日志，会持续增长。默认保留 30 天
（VOICE_EVENT_RETENTION_DAYS），由应用的常驻任务每天跑一次，
也可以在后台房间页手动触发。

设计取舍：
- 分批删除（每批 2000 行），避免一次删太多把 SQLite 写锁占太久，
  影响正在进行的语音识别落库。
- 清理失败只记日志，绝不影响应用启动或语音主流程。
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta

from sqlalchemy import delete, func, select

from app.clock import utcnow
from app.config import get_settings
from app.db import get_session_factory
from app.models import VoiceEvent, VoiceSegment, VoiceSession

BATCH_SIZE = 2000
# 启动后先等一会儿再跑第一次，避免和启动预热抢资源
INITIAL_DELAY_SECONDS = 120
INTERVAL_SECONDS = 24 * 3600


def cleanup_voice_logs(*, retention_days: int | None = None) -> dict[str, int]:
    """删除超过保留期的语音段落与事件。返回各表删除行数。"""
    days = retention_days if retention_days is not None else get_settings().voice_event_retention_days
    if days <= 0:
        return {"segments": 0, "events": 0, "sessions": 0, "skipped": 1}

    cutoff = utcnow() - timedelta(days=days)
    removed = {"segments": 0, "events": 0, "sessions": 0}

    # 事件先删（它引用 segment/session 的 id，但都是普通列，没有外键级联约束）
    removed["events"] = _delete_older_than(VoiceEvent, cutoff)
    removed["segments"] = _delete_older_than(VoiceSegment, cutoff)
    # 会话只有在没有任何段落残留时才删，保留最近有内容的会话
    removed["sessions"] = _delete_orphan_sessions(cutoff)
    return removed


def _delete_older_than(model, cutoff) -> int:
    total = 0
    while True:
        session = get_session_factory()()
        try:
            ids = [
                row[0]
                for row in session.execute(
                    select(model.id).where(model.created_at < cutoff).limit(BATCH_SIZE)
                )
            ]
            if not ids:
                return total
            session.execute(delete(model).where(model.id.in_(ids)))
            session.commit()
            total += len(ids)
            if len(ids) < BATCH_SIZE:
                return total
        except Exception:
            with contextlib.suppress(Exception):
                session.rollback()
            return total
        finally:
            session.close()


def _delete_orphan_sessions(cutoff) -> int:
    removed = 0
    while True:
        session = get_session_factory()()
        try:
            rows = session.execute(
                select(VoiceSession.id)
                .where(VoiceSession.started_at < cutoff)
                .where(
                    ~select(VoiceSegment.id)
                    .where(VoiceSegment.session_id == VoiceSession.id)
                    .exists()
                )
                .limit(BATCH_SIZE)
            ).all()
            ids = [row[0] for row in rows]
            if not ids:
                return removed
            session.execute(delete(VoiceSession).where(VoiceSession.id.in_(ids)))
            session.commit()
            removed += len(ids)
            if len(ids) < BATCH_SIZE:
                return removed
        except Exception:
            with contextlib.suppress(Exception):
                session.rollback()
            return removed
        finally:
            session.close()


def voice_log_stats() -> dict[str, int]:
    """给后台展示当前日志规模。"""
    session = get_session_factory()()
    try:
        return {
            "segments": int(session.scalar(select(func.count(VoiceSegment.id))) or 0),
            "events": int(session.scalar(select(func.count(VoiceEvent.id))) or 0),
            "sessions": int(session.scalar(select(func.count(VoiceSession.id))) or 0),
        }
    finally:
        session.close()


async def voice_cleanup_loop() -> None:
    """常驻循环：启动后等 2 分钟跑一次，之后每 24 小时一次。"""
    await asyncio.sleep(INITIAL_DELAY_SECONDS)
    while True:
        try:
            result = await asyncio.to_thread(cleanup_voice_logs)
            if result.get("segments") or result.get("events"):
                print(
                    f"[voice] 日志清理完成：段落 {result['segments']} 行、"
                    f"事件 {result['events']} 行、会话 {result['sessions']} 行"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 清理失败绝不能拖垮应用
            print(f"[voice] 日志清理失败：{type(exc).__name__}: {exc}")
        await asyncio.sleep(INTERVAL_SECONDS)

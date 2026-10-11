from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

# 业务统一使用上海时区（UTC+8）划分自然日与展示时间
SHANGHAI_TZ = timezone(timedelta(hours=8))


def utcnow() -> datetime:
    """返回 naive UTC 当前时间（替代已弃用的 datetime.utcnow）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def now_shanghai() -> datetime:
    """返回带时区的上海当前时间。"""
    return datetime.now(SHANGHAI_TZ)


def to_shanghai(moment: datetime) -> datetime:
    """把 naive UTC（或带时区）时间转换为带时区的上海时间，用于展示与导出。"""
    aware = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
    return aware.astimezone(SHANGHAI_TZ)


def shanghai_day_start_utc(moment: datetime | None = None) -> datetime:
    """返回上海时区当天 00:00 对应的 naive UTC 时间。

    数据库中的 created_at/updated_at 存的是 naive UTC，按上海自然日过滤时，
    要先取上海当天零点，再换算回 UTC 才能正确比较。
    """
    current = moment or utcnow()
    current_utc = current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)
    local_start = current_utc.astimezone(SHANGHAI_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(UTC).replace(tzinfo=None)

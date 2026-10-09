from datetime import UTC, datetime, timedelta

from app.clock import SHANGHAI_TZ, shanghai_day_start_utc, to_shanghai


def test_shanghai_day_start_before_utc_midnight() -> None:
    # UTC 2026-10-08 15:59 == 上海 2026-10-08 23:59，仍属于上海的 10-08
    moment = datetime(2026, 10, 8, 15, 59, tzinfo=UTC)
    assert shanghai_day_start_utc(moment) == datetime(2026, 10, 7, 16, 0)


def test_shanghai_day_start_at_shanghai_midnight() -> None:
    # UTC 2026-10-08 16:00 == 上海 2026-10-09 00:00，进入上海的新一天
    moment = datetime(2026, 10, 8, 16, 0, tzinfo=UTC)
    assert shanghai_day_start_utc(moment) == datetime(2026, 10, 8, 16, 0)


def test_shanghai_day_start_accepts_naive_utc() -> None:
    # 传入 naive UTC 时按 UTC 解读，换算回 UTC 的上海当日零点
    moment = datetime(2026, 10, 9, 3, 0)
    assert shanghai_day_start_utc(moment) == datetime(2026, 10, 8, 16, 0)


def test_to_shanghai_converts_naive_utc() -> None:
    assert to_shanghai(datetime(2026, 10, 9, 3, 0)) == datetime(2026, 10, 9, 11, 0, tzinfo=SHANGHAI_TZ)


def test_to_shanghai_keeps_aware_instant() -> None:
    moment = datetime(2026, 10, 9, 3, 0, tzinfo=UTC)
    assert to_shanghai(moment) == datetime(2026, 10, 9, 11, 0, tzinfo=SHANGHAI_TZ)
    assert to_shanghai(moment).utcoffset() == timedelta(hours=8)

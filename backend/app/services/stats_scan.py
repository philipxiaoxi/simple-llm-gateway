"""统计扫描的分块协作工具。

大表聚合按时间窗分块执行。每块开始前获取公平锁，块结束后回滚只读事务并释放锁，
让其他任务和在线查询有机会插队。锁按 FIFO 排队，避免某个长任务长期抢占。
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections import deque
from collections.abc import Iterator
from datetime import datetime, timedelta

# 单块覆盖的天数。块越小，单次占用数据库的时间越短，但查询次数会变多。
STATS_CHUNK_DAYS = 7


class FairLock:
    """进程内 FIFO 互斥锁。

    threading.Lock 不保证等待顺序；多个统计任务同时运行时，后到的任务可能一直抢不到。
    这里显式维护等待队列，先到先得。锁只在内存中有效，单进程 uvicorn 足够。
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._waiters: deque[object] = deque()
        self._holder: object | None = None

    def __enter__(self) -> None:
        token = object()
        with self._condition:
            self._waiters.append(token)
            try:
                while self._holder is not None or self._waiters[0] is not token:
                    self._condition.wait()
                self._waiters.popleft()
                self._holder = token
            except BaseException:
                with contextlib.suppress(ValueError):
                    self._waiters.remove(token)
                self._condition.notify_all()
                raise

    def __exit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        with self._condition:
            self._holder = None
            self._condition.notify_all()


STATS_SCAN_LOCK = FairLock()


def iter_time_chunks(start: datetime, end: datetime, *, days: int = STATS_CHUNK_DAYS) -> Iterator[tuple[datetime, datetime]]:
    """把 [start, end) 切成按自然时间连续的小块。"""
    if end <= start:
        return
    step = timedelta(days=max(1, int(days)))
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + step, end)
        yield cursor, chunk_end
        cursor = chunk_end


def yield_to_others(seconds: float = 0) -> None:
    """块间主动让出 CPU/数据库；默认只做线程调度。"""
    if seconds > 0:
        time.sleep(seconds)
    else:
        time.sleep(0)

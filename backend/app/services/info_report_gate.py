from __future__ import annotations

from threading import Lock
from time import monotonic

from app.config import get_settings

# 单进程内存滑窗限流。多 worker 时请在反代再限一次（同 login_gate 约定）。
WINDOW_SECONDS = 60.0


class InfoReportRateLimited(Exception):
    pass


class SlidingWindowGate:
    def __init__(self) -> None:
        self._lock = Lock()
        self._hits: dict[str, list[float]] = {}

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def check(self, key: str) -> None:
        """记录一次调用；窗口内超过上限时抛 InfoReportRateLimited。"""
        limit = int(get_settings().info_report_rate_per_minute or 0)
        if limit <= 0:
            return
        now = monotonic()
        window_start = now - WINDOW_SECONDS
        with self._lock:
            stamps = [stamp for stamp in self._hits.get(key, []) if stamp >= window_start]
            if len(stamps) >= limit:
                self._hits[key] = stamps
                raise InfoReportRateLimited
            stamps.append(now)
            self._hits[key] = stamps


info_report_gate = SlidingWindowGate()


def reset_info_report_gate() -> None:
    info_report_gate.reset()

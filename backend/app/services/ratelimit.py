"""基于内存的滑动窗口 RPM 限流与排队。

每个上游账号一个限流器，限制每分钟（60 秒滑动窗口）内最多发起的请求数（RPM）。
- rpm_limit == 0 表示无限制，请求直接放行。
- 窗口内已占用配额达到容量时，新请求进入等待队列，等待窗口滑动腾出配额或超时。
- 等待最长时间由调用方传入（默认 5 分钟），超时抛出 RateLimitTimeout。

与"并发信号量"不同，这里限制的是**每分钟的请求次数**（对齐上游 RPM 限流）：
- 每个请求在 acquire 时记录时间戳，占用一个配额。
- 配额随窗口滑动（时间流逝）自动释放，而不是请求完成时释放。
- release() 用于请求未真正发出（认证失败、取消等）时归还配额，避免配额浪费。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

# 滑动窗口长度（秒）
WINDOW_SECONDS = 60.0
# 等待最长时间（秒）
DEFAULT_WAIT_TIMEOUT_SECONDS = 300.0


class RateLimitTimeout(Exception):
    """等待限流配额超时。"""


@dataclass
class RpmLimiter:
    """单个上游账号的滑动窗口 RPM 限流器。

    注意：asyncio.Condition 会绑定到创建它时的事件循环。为避免在模块导入
    或 lifespan 阶段创建时绑定到错误的 loop，这里惰性创建，确保每次都在
    当前运行的事件循环中使用。
    """

    account_id: int
    capacity: int = 0  # 0 表示无限制
    window_seconds: float = WINDOW_SECONDS
    _timestamps: deque[float] = field(default_factory=deque)  # 窗口内已接受的请求时间戳
    waiting: int = 0  # 当前等待中的请求数
    _condition: asyncio.Condition | None = None
    _total_wait_ms: float = 0.0  # 累计等待毫秒（用于计算平均等待）
    _completed_waiting: int = 0  # 已完成等待的请求数

    @property
    def active(self) -> int:
        """窗口内当前已占用的配额数（即窗口内的请求数）。"""
        return len(self._timestamps)

    def _ensure_condition(self) -> asyncio.Condition:
        if self._condition is None:
            self._condition = asyncio.Condition()
        return self._condition

    def set_capacity(self, capacity: int) -> None:
        self.capacity = max(0, int(capacity))

    def _prune(self) -> None:
        """移除窗口外的旧时间戳，释放配额。"""
        cutoff = time.time() - self.window_seconds
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    def _time_until_slot(self) -> float:
        """窗口满时，最早的时间戳过期还需多久（秒）。"""
        if not self._timestamps:
            return 0.0
        oldest = self._timestamps[0]
        return max(0.0, (oldest + self.window_seconds) - time.time())

    async def acquire(self, timeout: float = DEFAULT_WAIT_TIMEOUT_SECONDS) -> None:
        """获取一个 RPM 配额。无限制时立即返回；超时抛 RateLimitTimeout。

        若在等待期间任务被取消（例如客户端断开），会正确回滚：等待计数递减，
        且若已获得配额则一并归还，避免配额泄漏。
        """
        if self.capacity <= 0:
            return
        condition = self._ensure_condition()
        deadline = time.monotonic() + timeout
        async with condition:
            while True:
                self._prune()
                if len(self._timestamps) < self.capacity:
                    self._timestamps.append(time.time())
                    return
                # 窗口已满，需要等待窗口滑动腾出配额
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RateLimitTimeout()
                self.waiting += 1
                waited_started = time.perf_counter()
                acquired = False
                try:
                    # 等待到最早时间戳过期，或剩余超时；被唤醒后重新检查。
                    # wait_until 至少 10ms，避免窗口刚好到期时空转。
                    wait_until = min(remaining, max(self._time_until_slot(), 0.01))
                    try:
                        await asyncio.wait_for(condition.wait(), timeout=wait_until)
                    except asyncio.TimeoutError:
                        continue  # 窗口可能已滑动，重新检查
                    self._prune()
                    if len(self._timestamps) < self.capacity:
                        self._timestamps.append(time.time())
                        acquired = True
                        return
                finally:
                    self.waiting -= 1
                    self._total_wait_ms += (time.perf_counter() - waited_started) * 1000
                    self._completed_waiting += 1
                    # 若已获得配额但任务随后被取消（CancelledError 在 async with 退出时抛出），
                    # 归还配额，避免 active 计数泄漏导致后续请求永久排队。
                    if acquired and asyncio.current_task() is not None and asyncio.current_task().cancelling():
                        if self._timestamps:
                            self._timestamps.popleft()
                        condition.notify(1)

    async def release(self) -> None:
        """归还未使用的配额（移除最早的时间戳），并唤醒一个等待者。

        仅用于请求未真正发往上游（认证失败、准备凭据失败、等待期间取消等）。
        已发往上游的请求必须占用窗口配额，直到 60 秒滑动窗口过期，不能在完成时归还。
        """
        if self.capacity <= 0:
            return
        condition = self._ensure_condition()
        async with condition:
            if self._timestamps:
                self._timestamps.popleft()
            if self.waiting > 0:
                condition.notify(1)

    def status(self) -> dict[str, object]:
        """返回当前占用/等待/容量状态。"""
        self._prune()
        return {
            "account_id": self.account_id,
            "capacity": self.capacity,
            "active": len(self._timestamps),
            "waiting": self.waiting,
            "usage_percent": round(len(self._timestamps) / self.capacity * 100, 1) if self.capacity > 0 else 0.0,
            "avg_wait_ms": round(self._total_wait_ms / self._completed_waiting, 1)
            if self._completed_waiting > 0
            else 0.0,
            # 距最早一个配额过期（窗口滑动腾出空间）还需多久；窗口未满时为 0
            "next_slot_in_ms": round(self._time_until_slot() * 1000, 1),
        }


# 全局限流器注册表：account_id -> RpmLimiter
_limiters: dict[int, RpmLimiter] = {}


def get_limiter(account_id: int, capacity: int = 0) -> RpmLimiter:
    limiter = _limiters.get(account_id)
    if limiter is None:
        limiter = RpmLimiter(account_id=account_id, capacity=capacity)
        _limiters[account_id] = limiter
    else:
        limiter.set_capacity(capacity)
    return limiter


def remove_limiter(account_id: int) -> None:
    _limiters.pop(account_id, None)


def all_status() -> list[dict[str, object]]:
    return [limiter.status() for limiter in _limiters.values()]


def summary() -> dict[str, object]:
    """概览页汇总：执行中、等待中、平均等待时间。"""
    active = 0
    waiting = 0
    total_wait_ms = 0.0
    completed_waiting = 0
    for limiter in _limiters.values():
        active += limiter.active
        waiting += limiter.waiting
        total_wait_ms += limiter._total_wait_ms
        completed_waiting += limiter._completed_waiting
    return {
        "active_requests": active,
        "waiting_requests": waiting,
        "avg_wait_ms": round(total_wait_ms / completed_waiting, 1) if completed_waiting > 0 else 0.0,
    }

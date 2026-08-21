"""基于内存的 RPM 限流与排队。

每个上游账号一个限流器，限制同一时刻并发执行中的请求数（RPM）。
- rpm_limit == 0 表示无限制，请求直接放行。
- 超过容量时请求进入等待队列，等待有槽位释放或超时。
- 等待最长时间由调用方传入（默认 5 分钟），超时抛出 RateLimitTimeout。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

# 等待最长时间（秒）
DEFAULT_WAIT_TIMEOUT_SECONDS = 300.0


class RateLimitTimeout(Exception):
    """等待限流槽位超时。"""


@dataclass
class RpmLimiter:
    """单个上游账号的限流器。

    注意：asyncio.Condition 会绑定到创建它时的事件循环。为避免在模块导入
    或 lifespan 阶段创建时绑定到错误的 loop，这里惰性创建，确保每次都在
    当前运行的事件循环中使用。
    """

    account_id: int
    capacity: int = 0  # 0 表示无限制
    active: int = 0  # 当前执行中的请求数
    waiting: int = 0  # 当前等待中的请求数
    _condition: asyncio.Condition | None = None
    _total_wait_ms: float = 0.0  # 累计等待毫秒（用于计算平均等待）
    _completed_waiting: int = 0  # 已完成等待的请求数

    def _ensure_condition(self) -> asyncio.Condition:
        if self._condition is None:
            self._condition = asyncio.Condition()
        return self._condition

    def set_capacity(self, capacity: int) -> None:
        self.capacity = max(0, int(capacity))

    async def acquire(self, timeout: float = DEFAULT_WAIT_TIMEOUT_SECONDS) -> None:
        """获取一个执行槽位。无限制时立即返回；超时抛 RateLimitTimeout。

        若在等待期间任务被取消（例如客户端断开），会正确回滚：等待计数递减，
        且若已获得槽位则一并释放，避免槽位泄漏。
        """
        if self.capacity <= 0:
            return
        condition = self._ensure_condition()
        async with condition:
            if self.active < self.capacity:
                self.active += 1
                return
            # 需要等待
            self.waiting += 1
            waited_started = time.perf_counter()
            acquired = False
            try:
                try:
                    await asyncio.wait_for(condition.wait(), timeout=timeout)
                except asyncio.TimeoutError as error:
                    raise RateLimitTimeout() from error
                # 被唤醒后获得槽位
                self.active += 1
                acquired = True
            finally:
                self.waiting -= 1
                self._total_wait_ms += (time.perf_counter() - waited_started) * 1000
                self._completed_waiting += 1
                # 若已获得槽位但任务随后被取消（CancelledError 在 async with 退出时抛出），
                # 回滚槽位，避免 active 计数泄漏导致后续请求永久排队。
                if acquired and asyncio.current_task() is not None and asyncio.current_task().cancelling():
                    self.active = max(0, self.active - 1)

    async def release(self) -> None:
        """释放一个执行槽位，并唤醒一个等待者。"""
        if self.capacity <= 0:
            return
        condition = self._ensure_condition()
        async with condition:
            self.active = max(0, self.active - 1)
            if self.waiting > 0:
                condition.notify(1)

    def status(self) -> dict[str, object]:
        """返回当前占用/等待/容量状态。"""
        return {
            "account_id": self.account_id,
            "capacity": self.capacity,
            "active": self.active,
            "waiting": self.waiting,
            "usage_percent": round(self.active / self.capacity * 100, 1) if self.capacity > 0 else 0.0,
            "avg_wait_ms": round(self._total_wait_ms / self._completed_waiting, 1)
            if self._completed_waiting > 0
            else 0.0,
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

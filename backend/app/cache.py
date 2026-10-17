"""进程内 TTL 缓存。

管理端的统计聚合、定时任务快照这类查询要扫全表，不适合每次请求都实时算。
这里的缓存只在**请求到来且条目已过期**时才重算（惰性刷新），不挂后台定时任务；
同一 key 的并发未命中用单飞锁合并，避免多人同时打开页面时重复扫库。

TTL 通过 callable 读取配置，方便测试与环境变量在运行时调整；TTL <= 0 表示关闭缓存。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class CacheEntry(Generic[T]):
    value: T
    stored_at: float
    expires_at: float

    def is_fresh(self, now: float) -> bool:
        return now < self.expires_at


class TTLCache(Generic[T]):
    """线程安全的进程内 TTL 缓存（管理端读多写少，不需要分布式一致性）。

    - `ttl` 可以是数字，也可以是每次命中时读取的 callable（便于热更新配置）；
    - `get_or_compute` 命中即返回，过期后才执行 factory，且同一 key 只有一个线程在算；
    - 条目数超过 `max_entries` 时按写入时间淘汰最旧的，并顺带清掉已过期条目。
    """

    def __init__(
        self,
        name: str,
        *,
        ttl: Callable[[], float] | float,
        max_entries: int = 32,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self._ttl = ttl
        self._max_entries = max(1, int(max_entries))
        self._clock = clock
        self._lock = threading.RLock()
        self._entries: dict[object, CacheEntry[T]] = {}
        self._key_locks: dict[object, threading.Lock] = {}
        self.hits = 0
        self.misses = 0

    def ttl_seconds(self) -> float:
        value = self._ttl() if callable(self._ttl) else self._ttl
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    def get(self, key: object) -> T | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if not entry.is_fresh(self._clock()):
                self._entries.pop(key, None)
                return None
            self.hits += 1
            return entry.value

    def entry(self, key: object) -> CacheEntry[T] | None:
        """返回仍有效的条目（含写入/到期时间），调试和测试用。"""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or not entry.is_fresh(self._clock()):
                return None
            return entry

    def set(self, key: object, value: T, *, ttl_seconds: float | None = None) -> None:
        ttl = self.ttl_seconds() if ttl_seconds is None else max(0.0, float(ttl_seconds))
        now = self._clock()
        with self._lock:
            self._entries[key] = CacheEntry(value=value, stored_at=now, expires_at=now + ttl)
            self._evict_locked(now)

    def get_or_compute(self, key: object, factory: Callable[[], T]) -> T:
        ttl = self.ttl_seconds()
        if ttl <= 0:
            with self._lock:
                self.misses += 1
            return factory()
        cached = self.get(key)
        if cached is not None:
            return cached
        with self._key_lock(key):
            cached = self.get(key)
            if cached is not None:
                return cached
            with self._lock:
                self.misses += 1
            value = factory()
            if value is not None:
                self.set(key, value, ttl_seconds=ttl)
            return value

    def delete(self, key: object) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def invalidate(self, predicate: Callable[[object], bool]) -> int:
        with self._lock:
            keys = [key for key in self._entries if predicate(key)]
            for key in keys:
                self._entries.pop(key, None)
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict[str, object]:
        with self._lock:
            return {
                "name": self.name,
                "entries": len(self._entries),
                "hits": self.hits,
                "misses": self.misses,
                "ttl_seconds": self.ttl_seconds(),
            }

    def _key_lock(self, key: object) -> threading.Lock:
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock

    def _evict_locked(self, now: float) -> None:
        for key in [key for key, entry in self._entries.items() if not entry.is_fresh(now)]:
            self._entries.pop(key, None)
        while len(self._entries) > self._max_entries:
            oldest_key = min(self._entries, key=lambda key: self._entries[key].stored_at)
            self._entries.pop(oldest_key, None)

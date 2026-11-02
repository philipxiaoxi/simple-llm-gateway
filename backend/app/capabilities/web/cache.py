from __future__ import annotations

import time
from collections import OrderedDict
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """有界 TTL 缓存，用于避免同一 URL 或查询反复打上游。"""

    def __init__(self, max_entries: int = 128) -> None:
        self.max_entries = max_entries
        self._data: OrderedDict[str, tuple[float, T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.time():
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: T, ttl: float) -> None:
        if ttl <= 0:
            return
        self._data[key] = (time.time() + ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()

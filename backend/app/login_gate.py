from __future__ import annotations

from threading import Lock
from time import monotonic

# 单进程内存限流。多 worker 时请在反代再限一次。
LOGIN_MAX_FAILURES = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
LOGIN_LOCKOUT_SECONDS = 15 * 60


class LoginLocked(Exception):
    pass


class LoginGate:
    def __init__(
        self,
        max_failures: int = LOGIN_MAX_FAILURES,
        window_seconds: int = LOGIN_FAILURE_WINDOW_SECONDS,
        lockout_seconds: int = LOGIN_LOCKOUT_SECONDS,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._lock = Lock()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()

    def check(self, username: str) -> None:
        key = _normalize_username(username)
        now = monotonic()
        with self._lock:
            locked_until = self._locked_until.get(key)
            if locked_until is not None and now < locked_until:
                raise LoginLocked
            if locked_until is not None:
                self._locked_until.pop(key, None)
                self._failures.pop(key, None)

    def fail(self, username: str) -> None:
        key = _normalize_username(username)
        now = monotonic()
        window_start = now - self.window_seconds
        with self._lock:
            stamps = [stamp for stamp in self._failures.get(key, []) if stamp >= window_start]
            stamps.append(now)
            self._failures[key] = stamps
            if len(stamps) >= self.max_failures:
                self._locked_until[key] = now + self.lockout_seconds

    def succeed(self, username: str) -> None:
        key = _normalize_username(username)
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


def _normalize_username(username: str) -> str:
    return username.strip().casefold()


login_gate = LoginGate()

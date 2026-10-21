from __future__ import annotations

import ipaddress
from threading import Lock
from time import monotonic

from app.config import get_settings

# 单进程内存限流。多 worker 时请在反代再限一次。
LOGIN_MAX_FAILURES = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
LOGIN_LOCKOUT_SECONDS = 15 * 60

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class LoginLocked(Exception):
    def __init__(self, retry_after: int = 0) -> None:
        super().__init__("too many failures")
        self.retry_after = max(int(retry_after), 0)


class LoginGate:
    """按来源（登录为 IP）计数的滑窗限流器。

    键与账号无关：同一来源的失败累计到阈值即锁定，避免用不同用户名绕过，
    也避免针对单个账号做定向锁定。
    """

    def __init__(
        self,
        max_failures: int = LOGIN_MAX_FAILURES,
        window_seconds: int = LOGIN_FAILURE_WINDOW_SECONDS,
        lockout_seconds: int = LOGIN_LOCKOUT_SECONDS,
        whitelist: str | None = None,
        exempt_loopback: bool = True,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self.whitelist = whitelist
        self.exempt_loopback = exempt_loopback
        self._lock = Lock()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()

    def is_exempt(self, client: str) -> bool:
        key = _normalize(client)
        if self.exempt_loopback and key in _LOOPBACK:
            return True
        raw = self.whitelist if self.whitelist is not None else get_settings().security_login_whitelist
        if not raw:
            return False
        parsed = _parse_ip(key)
        for item in raw.split(","):
            entry = item.strip()
            if not entry:
                continue
            if entry.casefold() == key:
                return True
            if parsed is None:
                continue
            network = _parse_network(entry)
            if network is not None and parsed in network:
                return True
        return False

    def check(self, client: str) -> None:
        key = _normalize(client)
        if self.is_exempt(key):
            return
        now = monotonic()
        with self._lock:
            locked_until = self._locked_until.get(key)
            if locked_until is not None and now < locked_until:
                raise LoginLocked(retry_after=int(locked_until - now) + 1)
            if locked_until is not None:
                self._locked_until.pop(key, None)
                self._failures.pop(key, None)

    def fail(self, client: str) -> None:
        key = _normalize(client)
        if self.is_exempt(key):
            return
        now = monotonic()
        window_start = now - self.window_seconds
        with self._lock:
            stamps = [stamp for stamp in self._failures.get(key, []) if stamp >= window_start]
            stamps.append(now)
            self._failures[key] = stamps
            if len(stamps) >= self.max_failures:
                self._locked_until[key] = now + self.lockout_seconds

    def succeed(self, client: str) -> None:
        key = _normalize(client)
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


def _normalize(value: str | None) -> str:
    return (value or "").strip().casefold()


def _parse_ip(value: str) -> ipaddress._BaseAddress | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _parse_network(value: str) -> ipaddress._BaseNetwork | None:
    try:
        return ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None


login_gate = LoginGate()

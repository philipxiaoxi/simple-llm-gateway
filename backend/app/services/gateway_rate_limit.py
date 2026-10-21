"""网关限速：/mcp 与 /v1 等路径按 Key 计 RPM 与并发。

单进程内存实现；多 worker 部署时每个 worker 各自计数，如需全局额度请在反代叠加限速。
额度来源为 Key 记录上的 rpm_limit / concurrency_limit，0 表示不限。
未携带有效 Key 的请求（Key 枚举场景）退化为按来源 IP 计 RPM。
"""

from __future__ import annotations

import json
from threading import Lock
from time import monotonic

from starlette.datastructures import Headers

from sqlalchemy import select

from app.config import get_settings
from app.crypto import hash_api_key
from app.db import get_session_factory
from app.deps import extract_raw_api_key
from app.models import ApiKey, McpKey
from app.services.client_meta import client_ip

WINDOW_SECONDS = 60.0
# 命中记录超过该量级时顺手清理已过期的键，避免长期驻留
_PRUNE_THRESHOLD = 5000

_GATEWAY_EXACT = frozenset({"/mcp", "/chat/completions", "/responses", "/models"})
_GATEWAY_PREFIX = ("/mcp/", "/v1/", "/anthropic/")


class GatewayLimited(Exception):
    def __init__(self, retry_after: int = 1) -> None:
        super().__init__("rate limited")
        self.retry_after = max(int(retry_after), 1)


def is_gateway_path(path: str) -> bool:
    return path in _GATEWAY_EXACT or path.startswith(_GATEWAY_PREFIX)


class GatewayRateLimiter:
    def __init__(self) -> None:
        self._lock = Lock()
        self._hits: dict[tuple[str, object], list[float]] = {}
        self._inflight: dict[tuple[str, object], int] = {}

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._inflight.clear()

    def acquire(self, kind: str, key_id: object, rpm_limit: int, concurrency_limit: int) -> None:
        key = (kind, key_id)
        now = monotonic()
        with self._lock:
            window = [stamp for stamp in self._hits.get(key, []) if stamp > now - WINDOW_SECONDS]
            if rpm_limit > 0 and len(window) >= rpm_limit:
                oldest = window[0]
                raise GatewayLimited(int(WINDOW_SECONDS - (now - oldest)) + 1)
            if concurrency_limit > 0 and self._inflight.get(key, 0) >= concurrency_limit:
                raise GatewayLimited(1)
            window.append(now)
            self._hits[key] = window
            self._inflight[key] = self._inflight.get(key, 0) + 1
            if len(self._hits) > _PRUNE_THRESHOLD:
                self._prune_locked(now)

    def release(self, kind: str, key_id: object) -> None:
        key = (kind, key_id)
        with self._lock:
            current = self._inflight.get(key, 0)
            if current <= 1:
                self._inflight.pop(key, None)
            else:
                self._inflight[key] = current - 1

    def usage(self, kind: str) -> dict[int, tuple[int, int]]:
        """返回 kind 下各 key 的 (近一分钟请求数, 在途并发数)。"""
        now = monotonic()
        result: dict[int, tuple[int, int]] = {}
        with self._lock:
            for (entry_kind, key_id), stamps in self._hits.items():
                if entry_kind != kind:
                    continue
                window = [stamp for stamp in stamps if stamp > now - WINDOW_SECONDS]
                result[int(key_id)] = (len(window), self._inflight.get((entry_kind, key_id), 0))
            for (entry_kind, key_id), count in self._inflight.items():
                if entry_kind == kind and int(key_id) not in result:
                    result[int(key_id)] = (0, count)
        return result

    def _prune_locked(self, now: float) -> None:
        for key in list(self._hits):
            if key not in self._inflight and not any(stamp > now - WINDOW_SECONDS for stamp in self._hits[key]):
                self._hits.pop(key, None)


gateway_limiter = GatewayRateLimiter()


def query_gateway_key(session, digest: str):
    """按 key_hash 查 ApiKey / McpKey，返回 (kind, id, rpm_limit, concurrency_limit)。"""
    row = session.scalar(select(ApiKey).where(ApiKey.key_hash == digest))
    if row is not None and row.status == "active":
        return ("api", row.id, row.rpm_limit, row.concurrency_limit)
    row = session.scalar(select(McpKey).where(McpKey.key_hash == digest))
    if row is not None and row.status == "active":
        return ("mcp", row.id, row.rpm_limit, row.concurrency_limit)
    return None


# 进程内解析缓存：命中时完全省掉一次建连接+索引查询（NullPool 下约 1.6ms）。
# 额度或状态变更时由管理端/服务层精确失效，因此 TTL 只作为兜底。
RESOLVE_TTL_SECONDS = 15.0
RESOLVE_MISS_TTL_SECONDS = 5.0
_RESOLVE_MAX_ENTRIES = 4096


class KeyResolveCache:
    def __init__(self) -> None:
        self._lock = Lock()
        # key_hash -> (过期时间, 解析结果或 None)
        self._entries: dict[str, tuple[float, tuple | None]] = {}

    def get(self, key_hash: str) -> tuple[tuple | None, bool]:
        now = monotonic()
        with self._lock:
            entry = self._entries.get(key_hash)
            if entry is None:
                return None, False
            expires_at, value = entry
            if expires_at <= now:
                self._entries.pop(key_hash, None)
                return None, False
            return value, True

    def put(self, key_hash: str, value: tuple | None) -> None:
        now = monotonic()
        ttl = RESOLVE_TTL_SECONDS if value is not None else RESOLVE_MISS_TTL_SECONDS
        with self._lock:
            if len(self._entries) >= _RESOLVE_MAX_ENTRIES:
                for stale in [key for key, (exp, _) in self._entries.items() if exp <= now]:
                    self._entries.pop(stale, None)
            if len(self._entries) >= _RESOLVE_MAX_ENTRIES:
                # 枚举会制造大量一次性负缓存，达到上限直接清空，避免无界增长
                self._entries.clear()
            self._entries[key_hash] = (now + ttl, value)

    def invalidate(self, key_hash: str) -> None:
        with self._lock:
            self._entries.pop(key_hash, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


resolve_cache = KeyResolveCache()


def invalidate_gateway_key(key_hash: str | None) -> None:
    """Key 的状态/额度发生变化时调用，立即丢弃其缓存条目。"""
    if key_hash:
        resolve_cache.invalidate(key_hash)


def clear_gateway_key_cache() -> None:
    resolve_cache.clear()


def resolve_gateway_key_cached(raw: str | None):
    """缓存优先的解析入口；未命中时才建 session 查库。

    查库失败按「无法识别」处理：限速器绝不能把网关请求打成 500。
    """
    if not raw:
        return None
    digest = hash_api_key(raw)
    cached, hit = resolve_cache.get(digest)
    if hit:
        return cached
    try:
        session = get_session_factory()()
    except Exception:
        return None
    try:
        value = query_gateway_key(session, digest)
    except Exception:
        return None
    finally:
        session.close()
    resolve_cache.put(digest, value)
    return value



async def _send_limited(send, retry_after: int) -> None:
    body = json.dumps(
        {"error": {"type": "rate_limit_error", "message": "请求过于频繁，请稍后再试"}},
        ensure_ascii=False,
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"retry-after", str(max(retry_after, 1)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class GatewayRateLimitMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not is_gateway_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        peer = (scope.get("client") or (None, None))[0]
        raw = extract_raw_api_key(headers.get("authorization"), headers.get("x-api-key"))
        resolved = resolve_gateway_key_cached(raw)

        if resolved is not None:
            kind, key_id, rpm_limit, concurrency_limit = resolved
        else:
            kind = "ip"
            key_id = client_ip(dict(headers), peer=peer) or "unknown"
            rpm_limit = get_settings().security_gateway_ip_rpm
            concurrency_limit = 0

        try:
            gateway_limiter.acquire(kind, key_id, rpm_limit, concurrency_limit)
        except GatewayLimited as error:
            await _send_limited(send, error.retry_after)
            return

        try:
            await self.app(scope, receive, send)
        finally:
            gateway_limiter.release(kind, key_id)

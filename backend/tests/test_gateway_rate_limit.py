from __future__ import annotations

import pytest

from app.services.gateway_rate_limit import GatewayLimited, GatewayRateLimiter, is_gateway_path


def test_rpm_limit_and_usage() -> None:
    limiter = GatewayRateLimiter()
    for _ in range(3):
        limiter.acquire("api", 1, 3, 0)
        limiter.release("api", 1)
    with pytest.raises(GatewayLimited) as error:
        limiter.acquire("api", 1, 3, 0)
    assert error.value.retry_after > 0
    used, inflight = limiter.usage("api")[1]
    assert used == 3
    assert inflight == 0


def test_concurrency_limit() -> None:
    limiter = GatewayRateLimiter()
    limiter.acquire("mcp", 7, 0, 1)
    with pytest.raises(GatewayLimited):
        limiter.acquire("mcp", 7, 0, 1)
    limiter.release("mcp", 7)
    limiter.acquire("mcp", 7, 0, 1)
    assert limiter.usage("mcp")[7] == (2, 1)


def test_zero_means_unlimited() -> None:
    limiter = GatewayRateLimiter()
    for _ in range(500):
        limiter.acquire("api", 2, 0, 0)
    assert limiter.usage("api")[2][0] == 500


def test_gateway_path_matcher() -> None:
    assert is_gateway_path("/mcp")
    assert is_gateway_path("/mcp/")
    assert is_gateway_path("/v1/chat/completions")
    assert is_gateway_path("/anthropic/v1/messages")
    assert is_gateway_path("/models")
    assert not is_gateway_path("/api/admin/keys")
    assert not is_gateway_path("/sites/demo/")


def test_invalid_key_requests_are_ip_limited(client, monkeypatch) -> None:
    monkeypatch.setenv("SECURITY_GATEWAY_IP_RPM", "3")
    from app.config import reset_settings

    reset_settings()
    for _ in range(3):
        assert client.get("/v1/models", headers={"authorization": "Bearer bad-key"}).status_code == 401
    limited = client.get("/v1/models", headers={"authorization": "Bearer bad-key"})
    assert limited.status_code == 429
    assert limited.headers.get("retry-after")


def test_resolve_cache_get_put_invalidate() -> None:
    from app.services.gateway_rate_limit import KeyResolveCache

    cache = KeyResolveCache()
    assert cache.get("h") == (None, False)
    cache.put("h", ("api", 1, 400, 200))
    value, hit = cache.get("h")
    assert hit is True
    assert value == ("api", 1, 400, 200)
    cache.invalidate("h")
    assert cache.get("h") == (None, False)


def test_resolve_cache_expires(monkeypatch) -> None:
    from app.services import gateway_rate_limit as mod

    clock = {"now": 1000.0}
    monkeypatch.setattr(mod, "monotonic", lambda: clock["now"])
    cache = mod.KeyResolveCache()
    cache.put("h", ("api", 1, 400, 200))
    clock["now"] += mod.RESOLVE_TTL_SECONDS - 1
    assert cache.get("h")[1] is True
    clock["now"] += 2
    assert cache.get("h") == (None, False)


def test_resolve_cache_negative_entries_are_bounded(monkeypatch) -> None:
    from app.services import gateway_rate_limit as mod

    clock = {"now": 1000.0}
    monkeypatch.setattr(mod, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(mod, "_RESOLVE_MAX_ENTRIES", 4)
    cache = mod.KeyResolveCache()
    for index in range(10):
        cache.put(f"h{index}", None)
    assert len(cache._entries) <= 4


def test_limit_change_takes_effect_immediately(client, auth_headers) -> None:
    """改额度后缓存必须立刻失效，新额度立即生效。"""
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    ).json()["id"]
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "限速缓存", "account_id": account, "rpm_limit": 2},
    ).json()
    key = created["key"]
    headers = {"Authorization": f"Bearer {key}"}

    assert client.get("/v1/models", headers=headers).status_code != 429
    assert client.get("/v1/models", headers=headers).status_code != 429
    assert client.get("/v1/models", headers=headers).status_code == 429

    # 额度上调后，缓存失效，下一次请求立刻放行
    client.patch(f"/api/admin/keys/{created['id']}", headers=auth_headers, json={"rpm_limit": 50})
    assert client.get("/v1/models", headers=headers).status_code != 429

    # 停用后，缓存失效，随即按无效 Key 处理（不再是 200/403，而是 401）
    client.patch(f"/api/admin/keys/{created['id']}", headers=auth_headers, json={"status": "disabled"})
    assert client.get("/v1/models", headers=headers).status_code == 401


def test_resolve_fails_open_on_db_error(client, monkeypatch) -> None:
    """查库异常时按「无法识别」处理，绝不能把网关请求打成 500。"""
    from app.services import gateway_rate_limit as mod

    mod.clear_gateway_key_cache()

    def boom(session, digest):
        raise RuntimeError("db down")

    monkeypatch.setattr(mod, "query_gateway_key", boom)
    assert mod.resolve_gateway_key_cached("sk-x") is None

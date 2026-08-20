from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.services.ratelimit import RateLimitTimeout, get_limiter, remove_limiter, summary


def test_unlimited_capacity_immediate() -> None:
    limiter = get_limiter(999001, capacity=0)
    asyncio.run(limiter.acquire())
    # 无限制时不跟踪 active，直接放行
    assert limiter.active == 0
    asyncio.run(limiter.release())
    assert limiter.active == 0


def test_capacity_blocks_and_releases() -> None:
    limiter = get_limiter(999002, capacity=1)

    async def scenario() -> None:
        await limiter.acquire()
        assert limiter.active == 1

        # 第二个请求应等待
        second_done = asyncio.Event()

        async def second() -> None:
            await limiter.acquire(timeout=0.5)
            assert limiter.active == 1  # capacity=1，窗口内最多 1 个配额
            second_done.set()

        task = asyncio.create_task(second())
        await asyncio.sleep(0.05)
        assert not second_done.is_set()  # 仍在等待
        assert limiter.waiting == 1

        # 归还未使用的配额，唤醒等待者
        await limiter.release()
        await asyncio.wait_for(task, timeout=1)
        assert second_done.is_set()
        assert limiter.active == 1

    asyncio.run(scenario())


def test_quota_held_until_window_expires() -> None:
    """已占用的配额不因时间未到而释放；窗口过期后才腾出。"""
    limiter = get_limiter(999005, capacity=1)
    limiter.window_seconds = 0.2

    async def scenario() -> None:
        await limiter.acquire()
        assert limiter.active == 1
        with pytest.raises(RateLimitTimeout):
            await limiter.acquire(timeout=0.05)
        assert limiter.active == 1

        await limiter.acquire(timeout=0.5)
        assert limiter.active == 1

    asyncio.run(scenario())
    remove_limiter(999005)


def test_timeout_raises() -> None:
    limiter = get_limiter(999003, capacity=1)

    async def scenario() -> None:
        await limiter.acquire()
        with pytest.raises(RateLimitTimeout):
            await limiter.acquire(timeout=0.1)
        await limiter.release()

    asyncio.run(scenario())


def test_status_and_summary() -> None:
    limiter = get_limiter(999004, capacity=10)
    asyncio.run(limiter.acquire())
    status = limiter.status()
    assert status["active"] == 1
    assert status["capacity"] == 10
    assert status["usage_percent"] == 10.0

    summ = summary()
    assert summ["active_requests"] >= 1
    asyncio.run(limiter.release())
    remove_limiter(999004)


def test_account_rpm_field(client: TestClient, auth_headers: dict[str, str]) -> None:
    """创建账号时设置 rpm_limit，并能在列表里读到。"""
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok", "rpm_limit": 30},
    ).json()
    assert account["rpm_limit"] == 30

    # 更新 rpm_limit
    updated = client.patch(
        f"/api/admin/accounts/{account['id']}",
        headers=auth_headers,
        json={"rpm_limit": 15},
    ).json()
    assert updated["rpm_limit"] == 15

    # 限流状态接口
    status = client.get("/api/admin/ratelimit/status", headers=auth_headers).json()
    assert any(item["account_id"] == account["id"] and item["capacity"] == 15 for item in status)


def test_grok_default_rpm(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Grok 账号默认 30 RPM。"""
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok2", "provider": "grok"},
    ).json()
    assert account["rpm_limit"] == 30


def test_opencode_go_default_rpm(client: TestClient, auth_headers: dict[str, str]) -> None:
    """OpenCode Go 默认无限制（0）。"""
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "OC", "provider": "opencode_go", "api_key": "sk-oc"},
    ).json()
    assert account["rpm_limit"] == 0

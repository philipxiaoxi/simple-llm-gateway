"""验证客户端断开/取消请求时，限流槽位与上游资源能正确释放，避免浪费额度。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.services.ratelimit import get_limiter, remove_limiter


def _make_key(client: TestClient, auth_headers: dict[str, str], rpm_limit: int = 1) -> str:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up", "rpm_limit": rpm_limit},
    ).json()
    return client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account["id"]},
    ).json()["key"]


def test_non_stream_cancel_releases_slot(client: TestClient, auth_headers: dict[str, str]) -> None:
    """非流式请求在上游调用期间被取消，槽位必须释放（不能泄漏）。"""
    key = _make_key(client, auth_headers, rpm_limit=1)
    account_id = client.get("/api/admin/accounts", headers=auth_headers).json()[0]["id"]
    limiter = get_limiter(account_id, 1)

    # 先占满唯一槽位
    asyncio.run(limiter.acquire())
    assert limiter.active == 1

    # 模拟一个阻塞的上游调用
    async def blocking_call(*_args, **_kwargs):
        await asyncio.Event().wait()  # 永不返回，直到被取消

    from app.services import proxy as proxy_service

    async def scenario() -> None:
        from app.db import get_session_factory
        from app.models import ApiKey, UpstreamAccount

        session = get_session_factory()()
        try:
            api_key = session.query(ApiKey).first()
            account = session.query(UpstreamAccount).first()
            body = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]}
            task = asyncio.create_task(
                proxy_service.handle_chat(
                    session,
                    api_key,
                    account,
                    body,
                    "openai_chat",
                    request_headers=None,
                    release_slot=limiter.release,
                )
            )
            # 让 handle_chat 进入 call_chat 阻塞
            await asyncio.sleep(0.2)
            # 模拟客户端断开：取消任务
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            session.close()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(proxy_service, "call_chat", blocking_call)
        asyncio.run(scenario())

    # 槽位必须已释放
    assert limiter.active == 0, f"槽位泄漏：active={limiter.active}"
    remove_limiter(account_id)


def test_stream_cancel_releases_slot(client: TestClient, auth_headers: dict[str, str]) -> None:
    """流式请求在流式传输期间被取消，槽位必须释放。"""
    key = _make_key(client, auth_headers, rpm_limit=1)
    account_id = client.get("/api/admin/accounts", headers=auth_headers).json()[0]["id"]
    limiter = get_limiter(account_id, 1)

    asyncio.run(limiter.acquire())
    assert limiter.active == 1

    async def blocking_stream(*_args, **_kwargs):
        async def chunks():
            while True:
                await asyncio.sleep(0.05)
                yield {"choices": [{"delta": {"content": "x"}, "index": 0}]}

        return chunks()

    from app.services import proxy as proxy_service

    async def scenario() -> None:
        from app.db import get_session_factory
        from app.models import ApiKey, UpstreamAccount

        session = get_session_factory()()
        try:
            api_key = session.query(ApiKey).first()
            account = session.query(UpstreamAccount).first()
            body = {
                "model": "deepseek-chat",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            }
            response = await proxy_service.handle_chat(
                session,
                api_key,
                account,
                body,
                "openai_chat",
                request_headers=None,
                release_slot=limiter.release,
            )
            # 消费流式生成器，中途取消以模拟客户端断开
            gen = response.body_iterator
            await gen.__anext__()  # 开始流式传输
            assert limiter.active == 1  # 流式期间槽位仍被占用
            await gen.aclose()  # 模拟客户端断开：关闭生成器
        finally:
            session.close()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(proxy_service, "call_chat", blocking_stream)
        asyncio.run(scenario())

    assert limiter.active == 0, f"槽位泄漏：active={limiter.active}"
    remove_limiter(account_id)


def test_db_merge_error_releases_slot(client: TestClient, auth_headers: dict[str, str]) -> None:
    """db.merge 抛异常时，槽位必须释放（修复：merge 移入 try 块，except BaseException 释放）。"""
    _make_key(client, auth_headers, rpm_limit=1)
    account_id = client.get("/api/admin/accounts", headers=auth_headers).json()[0]["id"]
    limiter = get_limiter(account_id, 1)

    asyncio.run(limiter.acquire())
    assert limiter.active == 1

    from app.services import proxy as proxy_service

    async def scenario() -> None:
        from app.db import get_session_factory
        from app.models import ApiKey, UpstreamAccount

        session = get_session_factory()()
        try:
            api_key = session.query(ApiKey).first()
            account = session.query(UpstreamAccount).first()
            body = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]}
            # merge 抛异常应向上传播，且槽位必须已释放
            with pytest.raises(RuntimeError):
                await proxy_service.handle_chat(
                    session,
                    api_key,
                    account,
                    body,
                    "openai_chat",
                    request_headers=None,
                    release_slot=limiter.release,
                )
        finally:
            session.close()

    with pytest.MonkeyPatch.context() as mp:
        # 让 db.merge（经 run_db_read 调用）抛异常
        async def boom(*_args, **_kwargs):
            raise RuntimeError("merge failed")

        mp.setattr(proxy_service, "run_db_read", boom)
        asyncio.run(scenario())

    # 槽位必须已释放
    assert limiter.active == 0, f"槽位泄漏：active={limiter.active}"
    remove_limiter(account_id)


def test_stream_call_chat_cancelled_releases_slot(client: TestClient, auth_headers: dict[str, str]) -> None:
    """流式 call_chat 抛 CancelledError（客户端断开）时，槽位必须释放（修复：except BaseException）。"""
    _make_key(client, auth_headers, rpm_limit=1)
    account_id = client.get("/api/admin/accounts", headers=auth_headers).json()[0]["id"]
    limiter = get_limiter(account_id, 1)

    asyncio.run(limiter.acquire())
    assert limiter.active == 1

    from app.services import proxy as proxy_service

    async def cancelled_call(*_args, **_kwargs):
        raise asyncio.CancelledError()

    async def scenario() -> None:
        from app.db import get_session_factory
        from app.models import ApiKey, UpstreamAccount

        session = get_session_factory()()
        try:
            api_key = session.query(ApiKey).first()
            account = session.query(UpstreamAccount).first()
            body = {
                "model": "deepseek-chat",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            }
            # CancelledError 应向上传播，且槽位必须已释放
            with pytest.raises(asyncio.CancelledError):
                await proxy_service.handle_chat(
                    session,
                    api_key,
                    account,
                    body,
                    "openai_chat",
                    request_headers=None,
                    release_slot=limiter.release,
                )
        finally:
            session.close()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(proxy_service, "call_chat", cancelled_call)
        asyncio.run(scenario())

    # 槽位必须已释放
    assert limiter.active == 0, f"槽位泄漏：active={limiter.active}"
    remove_limiter(account_id)

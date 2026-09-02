from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.services.benchmark import compute_tokens_per_second, is_token_delta, output_tokens_from_chunk


def test_is_token_delta_ignores_empty_heartbeat() -> None:
    assert is_token_delta({"choices": [{"delta": {"role": "assistant", "content": None, "reasoning_content": ""}}]}) is False
    assert is_token_delta({"choices": [{"delta": {"content": ""}}]}) is False
    assert is_token_delta({"choices": []}) is False


def test_is_token_delta_accepts_reasoning_text_and_tool() -> None:
    assert is_token_delta({"choices": [{"delta": {"reasoning_content": "think"}}]}) is True
    assert is_token_delta({"choices": [{"delta": {"content": "hi"}}]}) is True
    assert is_token_delta({"choices": [{"delta": {"tool_calls": [{"function": {"name": "search"}}]}}]}) is True
    assert is_token_delta({"choices": [{"delta": {"tool_calls": [{"function": {"arguments": "{"}}]}}]}) is True


def test_output_tokens_use_completion_and_include_reasoning() -> None:
    assert output_tokens_from_chunk({
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 40,
            "completion_tokens_details": {"reasoning_tokens": 30},
        }
    }) == 40
    assert output_tokens_from_chunk({"usage": {"output_tokens": 12}}) == 12
    assert output_tokens_from_chunk({"choices": [{"delta": {"content": "x"}}]}) is None


def test_tokens_per_second_uses_decode_window_only() -> None:
    assert compute_tokens_per_second(100, 5_000) == 20
    assert compute_tokens_per_second(30, 2_000) == 15
    assert compute_tokens_per_second(None, 5_000) is None
    assert compute_tokens_per_second(10, 0) is None
    assert compute_tokens_per_second(10, -3) is None


def _account(client: TestClient, auth_headers: dict[str, str]) -> int:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    )
    assert created.status_code == 200
    return created.json()["id"]


def test_benchmark_follows_dsh_usage_and_decode_window(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    account_id = _account(client, auth_headers)
    captured: dict = {}

    async def fake_complete(_account, _messages, _model, stream, extra, _token):
        captured["stream"] = stream
        captured["extra"] = extra

        async def chunks():
            yield {"choices": [{"delta": {"role": "assistant", "content": None, "reasoning_content": ""}}]}
            yield {"choices": [{"delta": {"content": None, "reasoning_content": "先想一下"}}]}
            await asyncio.sleep(0.05)
            yield {"choices": [{"delta": {"content": "你好世界你好世界你好世界你好世界"}}]}
            yield {"choices": [], "usage": {"prompt_tokens": 8, "completion_tokens": 40, "total_tokens": 48}}

        return chunks()

    provider = AsyncMock()
    provider.complete = fake_complete
    with patch("app.routers.admin_benchmark.get_provider", return_value=provider):
        response = client.post(
            "/api/admin/benchmark",
            headers=auth_headers,
            json={"account_id": account_id, "model": "deepseek-reasoner", "prompt": "hi", "max_tokens": 64},
        )

    assert response.status_code == 200
    body = response.json()
    assert captured["stream"] is True
    assert captured["extra"]["stream_options"]["include_usage"] is True
    assert body["ok"] is True
    assert body["estimated_output_tokens"] == 40
    assert body["first_token_ms"] is not None
    assert body["total_ms"] > body["first_token_ms"]
    assert body["output_tokens_per_second"] == compute_tokens_per_second(
        40, body["total_ms"] - body["first_token_ms"]
    )
    char_estimate = round(len("你好世界你好世界你好世界你好世界") / 4)
    assert body["output_tokens_per_second"] != compute_tokens_per_second(
        char_estimate, body["total_ms"]
    )


def test_benchmark_omits_speed_without_usage(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)

    async def fake_complete(*_args, **_kwargs):
        async def chunks():
            yield {"choices": [{"delta": {"content": "只有正文"}}]}

        return chunks()

    provider = AsyncMock()
    provider.complete = fake_complete
    with patch("app.routers.admin_benchmark.get_provider", return_value=provider):
        response = client.post(
            "/api/admin/benchmark",
            headers=auth_headers,
            json={"account_id": account_id, "model": "deepseek-chat", "prompt": "hi", "max_tokens": 32},
        )

    body = response.json()
    assert body["ok"] is True
    assert body["output_tokens_per_second"] is None
    assert body["estimated_output_tokens"] == round(len("只有正文") / 4)


def test_benchmark_allows_disabled_model(client: TestClient, auth_headers: dict[str, str]) -> None:
    from unittest.mock import patch as http_patch

    account_id = _account(client, auth_headers)

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"data": [{"id": "deepseek-chat"}]}

    with http_patch("app.providers.base.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=FakeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        client.post(f"/api/admin/accounts/{account_id}/models", headers=auth_headers)

    client.patch(
        f"/api/admin/accounts/{account_id}/models/deepseek-chat",
        headers=auth_headers,
        json={"enabled": False},
    )

    async def fake_complete(*_args, **_kwargs):
        async def chunks():
            yield {"choices": [{"delta": {"content": "ok"}}]}
            yield {"usage": {"completion_tokens": 1}}

        return chunks()

    provider = AsyncMock()
    provider.complete = fake_complete
    with patch("app.routers.admin_benchmark.get_provider", return_value=provider):
        response = client.post(
            "/api/admin/benchmark",
            headers=auth_headers,
            json={"account_id": account_id, "model": "deepseek-chat", "prompt": "hi", "max_tokens": 32},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_benchmark_times_out_on_total_duration(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)

    async def fake_complete(*_args, **_kwargs):
        async def chunks():
            yield {"choices": [{"delta": {"content": "先出字"}}]}
            await asyncio.sleep(0.2)
            yield {"choices": [{"delta": {"content": "不该再等到这里"}}]}

        return chunks()

    provider = AsyncMock()
    provider.complete = fake_complete
    with (
        patch("app.routers.admin_benchmark.get_provider", return_value=provider),
        patch("app.routers.admin_benchmark.TOTAL_TIMEOUT_SECONDS", 0.05),
    ):
        response = client.post(
            "/api/admin/benchmark",
            headers=auth_headers,
            json={"account_id": account_id, "model": "deepseek-chat", "prompt": "hi", "max_tokens": 32},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["timeout"] is True
    assert "测速超过" in body["error"]


def test_dashboard_counts_saved_benchmark_runs(client: TestClient, auth_headers: dict[str, str]) -> None:
    empty = client.get("/api/admin/dashboard", headers=auth_headers)
    assert empty.status_code == 200
    assert empty.json()["benchmark_count"] == 0

    saved = client.post(
        "/api/admin/benchmark/history",
        headers=auth_headers,
        json={
            "prompt": "测速",
            "max_tokens": 32,
            "results": [
                {
                    "account_id": 1,
                    "account_name": "DS",
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "ok": True,
                    "timeout": False,
                    "first_token_ms": 120,
                    "total_ms": 800,
                    "output_chars": 16,
                    "estimated_output_tokens": 40,
                    "output_tokens_per_second": 50,
                    "preview": "ok",
                    "error": None,
                }
            ],
        },
    )
    assert saved.status_code == 200

    dashboard = client.get("/api/admin/dashboard", headers=auth_headers)
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["benchmark_count"] == 1
    assert body["benchmark_speed_top"] == [
        {
            "model": "deepseek-chat",
            "account_name": "DS",
            "provider": "deepseek",
            "output_tokens_per_second": 50.0,
            "first_token_ms": 120.0,
            "total_ms": 800.0,
            "run_id": saved.json()["id"],
            "created_at": saved.json()["created_at"],
        }
    ]


def test_dashboard_benchmark_speed_top_shows_recent_successful_runs(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    def save_run(model: str, speed: float, ok: bool = True) -> None:
        saved = client.post(
            "/api/admin/benchmark/history",
            headers=auth_headers,
            json={
                "prompt": "多模型",
                "max_tokens": 32,
                "results": [
                    {
                        "account_id": 1,
                        "account_name": "A",
                        "provider": "deepseek",
                        "model": model,
                        "ok": ok,
                        "timeout": False,
                        "first_token_ms": 100 if ok else None,
                        "total_ms": 500 if ok else None,
                        "output_chars": 10 if ok else None,
                        "estimated_output_tokens": 20 if ok else None,
                        "output_tokens_per_second": speed,
                        "preview": "a" if ok else None,
                        "error": None if ok else "boom",
                    }
                ],
            },
        )
        assert saved.status_code == 200
        return saved

    save_run("first", 10.0)
    save_run("second", 120.0)
    save_run("failed", 999, ok=False)

    top = client.get("/api/admin/dashboard", headers=auth_headers).json()["benchmark_speed_top"]
    assert [item["model"] for item in top] == ["second", "first"]
    assert [item["output_tokens_per_second"] for item in top] == [120.0, 10.0]
    assert top[0]["created_at"] is not None

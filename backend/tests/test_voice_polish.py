"""AI 纠错服务单测。

核心不变量：**任何失败都必须回退到 ASR 原文**，绝不能因为模型异常改坏用户内容。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import voice_polish
from app.services.voice_polish import (
    MODE_ERROR_FIX,
    MODE_OFF,
    MODE_REWRITE,
    clean_model_output,
    default_prompt,
    edit_distance_ratio,
    polish_text,
    should_apply,
)


class FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class FakeChoice:
    def __init__(self, content: str | None, finish_reason: str = "stop") -> None:
        self.message = FakeMessage(content)
        self.finish_reason = finish_reason


class FakeResponse:
    def __init__(self, content: str | None, finish_reason: str = "stop") -> None:
        self.choices = [FakeChoice(content, finish_reason)]


class FakeAccount:
    def __init__(self, account_id: int = 1, available: bool = True) -> None:
        self.id = account_id
        self.models_json = '[{"id": "test-model"}]'
        self._available = available


class FakeSession:
    """只实现 polish_text 用到的那一部分 Session 接口。"""

    def __init__(self, account: Any) -> None:
        self._account = account

    def get(self, _model: Any, _pk: Any) -> Any:
        return self._account


def _patch_deps(monkeypatch, *, account: Any = None, available: bool = True, response: Any = None, error: Exception | None = None):
    monkeypatch.setattr(voice_polish, "is_account_available", lambda _account: available)

    async def fake_prepare(_account, _db):
        return "credential"

    async def fake_call_chat(*_args, **_kwargs):
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(voice_polish, "prepare_credential", fake_prepare)
    monkeypatch.setattr(voice_polish, "call_chat", fake_call_chat)


# ---------------- 纯函数 ----------------


def test_clean_strips_quotes_and_prefixes() -> None:
    assert clean_model_output("润色后：你好，我们明天见。") == "你好，我们明天见。"
    assert clean_model_output("「你好，我们明天见。」") == "你好，我们明天见。"
    assert clean_model_output('"你好，我们明天见。"') == "你好，我们明天见。"
    assert clean_model_output("```\n你好，我们明天见。\n```") == "你好，我们明天见。"
    assert clean_model_output("结果: 你好") == "你好"
    assert clean_model_output("   ") == ""


def test_clean_keeps_only_first_content_line() -> None:
    assert clean_model_output("你好，我们明天见。\n\n说明：我修正了两个错别字。") == "你好，我们明天见。"


def test_edit_distance_ratio_bounds() -> None:
    assert edit_distance_ratio("abc", "abc") == 0.0
    assert edit_distance_ratio("", "") == 0.0
    assert edit_distance_ratio("abc", "xyz") == 1.0
    assert 0 < edit_distance_ratio("你好我们明天见", "你好，我们明天见") < 0.4


def test_should_apply_rejects_no_change() -> None:
    assert should_apply("你好", "你好") == (False, "no_change")


def test_should_apply_rejects_empty() -> None:
    assert should_apply("你好", "") == (False, "empty")


def test_should_apply_rejects_over_edited() -> None:
    raw = "你好我们明天见。"
    polished = "你好，关于我们明天下午的会面安排，我建议改到三点之后再进行讨论，你看如何？"
    ok, reason = should_apply(raw, polished)
    assert ok is False
    assert reason in {"too_long", "too_different"}


def test_should_apply_accepts_small_fix() -> None:
    ok, reason = should_apply("帮我看一下这个把，我们下周再讨论。", "帮我看一下这个吧，我们下周再讨论。")
    assert (ok, reason) == (True, "ok")


def test_rewrite_mode_is_more_permissive() -> None:
    raw = "嗯那个我想说就是我们明天下午三点开会这个事情吧就是说请大家都提前准备好方案"
    polished = "我想说，我们明天下午三点开会，请大家都提前准备好方案。"
    strict, _ = should_apply(raw, polished, MODE_ERROR_FIX)
    loose, _ = should_apply(raw, polished, MODE_REWRITE)
    assert (strict, loose) in {(False, True), (True, True)}


def test_default_prompt_switches_by_mode() -> None:
    assert "改写" in default_prompt(MODE_ERROR_FIX)
    assert default_prompt(MODE_REWRITE) != default_prompt(MODE_ERROR_FIX)
    assert "严禁删减" in default_prompt(MODE_ERROR_FIX)


# ---------------- 端到端（打桩模型） ----------------


@pytest.mark.asyncio
async def test_polish_ok(monkeypatch) -> None:
    _patch_deps(monkeypatch, account=FakeAccount(), response=FakeResponse("帮我看一下这个吧，我们下周再讨论。"))
    result = await polish_text(
        FakeSession(FakeAccount()),
        account_id=1,
        model="test-model",
        text="帮我看一下这个把，我们下周再讨论。",
        mode=MODE_ERROR_FIX,
    )
    assert result.status == "ok"
    assert result.text == "帮我看一下这个吧，我们下周再讨论。"
    assert result.raw == "帮我看一下这个把，我们下周再讨论。"
    assert result.model == "test-model"


@pytest.mark.asyncio
async def test_polish_falls_back_on_exception(monkeypatch) -> None:
    _patch_deps(monkeypatch, account=FakeAccount(), error=RuntimeError("上游 500"))
    raw = "帮我看一下这个把"
    result = await polish_text(FakeSession(FakeAccount()), account_id=1, model="m", text=raw)
    assert result.status == "failed"
    assert result.text == raw  # 必须回退原文
    assert "上游 500" in (result.error or "")


@pytest.mark.asyncio
async def test_polish_falls_back_on_timeout(monkeypatch) -> None:
    _patch_deps(monkeypatch, account=FakeAccount())
    import asyncio

    async def slow(*_args, **_kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(voice_polish, "call_chat", slow)
    raw = "帮我看一下这个把"
    result = await polish_text(FakeSession(FakeAccount()), account_id=1, model="m", text=raw, timeout=0.05)
    assert result.status == "failed"
    assert result.text == raw
    assert "超时" in (result.error or "")


@pytest.mark.asyncio
async def test_polish_reports_length_finish_reason(monkeypatch) -> None:
    """推理模型把 token 预算吃光时 content 为空，要给出可诊断的错误而不是静默失败。"""
    _patch_deps(monkeypatch, account=FakeAccount(), response=FakeResponse("", finish_reason="length"))
    raw = "帮我看一下这个把"
    result = await polish_text(FakeSession(FakeAccount()), account_id=1, model="m", text=raw)
    assert result.status == "failed"
    assert result.text == raw
    assert "max_tokens" in (result.error or "")


@pytest.mark.asyncio
async def test_polish_no_change_marks_status(monkeypatch) -> None:
    _patch_deps(monkeypatch, account=FakeAccount(), response=FakeResponse("帮我看一下这个把"))
    result = await polish_text(FakeSession(FakeAccount()), account_id=1, model="m", text="帮我看一下这个把")
    assert result.status == "no_change"
    assert result.text == "帮我看一下这个把"


@pytest.mark.asyncio
async def test_polish_over_edit_is_rejected(monkeypatch) -> None:
    _patch_deps(
        monkeypatch,
        account=FakeAccount(),
        response=FakeResponse("你好，关于我们明天下午的会面安排，我建议改到三点之后再进行讨论，你觉得怎么样呢？"),
    )
    raw = "你好我们明天见"
    result = await polish_text(FakeSession(FakeAccount()), account_id=1, model="m", text=raw)
    assert result.status in {"too_long", "too_different"}
    assert result.text == raw


@pytest.mark.asyncio
async def test_polish_skips_when_mode_off(monkeypatch) -> None:
    _patch_deps(monkeypatch, account=FakeAccount(), response=FakeResponse("改了"))
    result = await polish_text(FakeSession(FakeAccount()), account_id=1, model="m", text="原文", mode=MODE_OFF)
    assert result.status == "skipped"
    assert result.text == "原文"


@pytest.mark.asyncio
async def test_polish_skips_without_account(monkeypatch) -> None:
    _patch_deps(monkeypatch, account=None, response=FakeResponse("改了"))
    result = await polish_text(FakeSession(None), account_id=None, model="m", text="原文")
    assert result.status == "skipped"
    assert result.reason == "no_account"


@pytest.mark.asyncio
async def test_polish_skips_when_account_unavailable(monkeypatch) -> None:
    _patch_deps(monkeypatch, account=FakeAccount(), available=False, response=FakeResponse("改了"))
    result = await polish_text(FakeSession(FakeAccount()), account_id=1, model="m", text="原文")
    assert result.status == "skipped"
    assert result.reason == "account_unavailable"


@pytest.mark.asyncio
async def test_polish_handles_dict_response(monkeypatch) -> None:
    payload = {"choices": [{"message": {"content": "帮我看一下这个吧"}, "finish_reason": "stop"}]}
    _patch_deps(monkeypatch, account=FakeAccount(), response=payload)
    result = await polish_text(FakeSession(FakeAccount()), account_id=1, model="m", text="帮我看一下这个把")
    assert result.status == "ok"
    assert result.text == "帮我看一下这个吧"


@pytest.mark.asyncio
async def test_polish_context_is_passed(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_call_chat(_account, messages, *_args, **_kwargs):
        captured["messages"] = messages
        return FakeResponse("好的")

    monkeypatch.setattr(voice_polish, "is_account_available", lambda _a: True)

    async def fake_prepare(_account, _db):
        return "credential"

    monkeypatch.setattr(voice_polish, "prepare_credential", fake_prepare)
    monkeypatch.setattr(voice_polish, "call_chat", fake_call_chat)

    await polish_text(
        FakeSession(FakeAccount()),
        account_id=1,
        model="m",
        text="明天见",
        context=["我们约了三点开会", "地点在会议室"],
    )
    user_content = captured["messages"][1]["content"]
    assert "三点开会" in user_content
    assert "地点在会议室" in user_content
    assert "明天见" in user_content

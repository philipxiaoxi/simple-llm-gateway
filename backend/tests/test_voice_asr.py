"""阿里云实时 ASR 客户端单测：报文结构、事件解析、超时与失败降级。

不连真实网络，用假 WebSocket 驱动。真实链路自检见 scripts/voice_asr_probe.py。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.services.voice_asr import (
    AsrError,
    VoiceAsrSession,
    build_finish_task_payload,
    build_run_task_payload,
    parse_result_sentence,
    parse_usage_seconds,
)


class FakeWebSocket:
    """记录发出的帧，并把预先排好的服务端事件按顺序吐出来。"""

    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self.sent_text: list[dict[str, Any]] = []
        self.sent_bytes: list[bytes] = []
        self._events = list(events or [])
        self.closed = False
        self._started = asyncio.Event()

    async def send(self, payload: Any) -> None:
        if isinstance(payload, (bytes, bytearray)):
            self.sent_bytes.append(bytes(payload))
            return
        frame = json.loads(payload)
        self.sent_text.append(frame)
        if frame.get("header", {}).get("action") == "run-task":
            # 模拟服务端立刻回 task-started
            self._started.set()

    def __aiter__(self) -> "FakeWebSocket":
        return self

    async def __anext__(self) -> str:
        await self._started.wait()
        if not self._events:
            raise StopAsyncIteration
        return json.dumps(self._events.pop(0))

    async def close(self) -> None:
        self.closed = True


def _result_event(text: str, sentence_end: bool, begin: int | None = None, end: int | None = None) -> dict:
    return {
        "header": {"task_id": "t", "event": "result-generated", "attributes": {}},
        "payload": {
            "output": {"sentence": {"text": text, "sentence_end": sentence_end, "begin_time": begin, "end_time": end}},
            "usage": {"duration": 3} if sentence_end else None,
        },
    }


def test_run_task_payload_shape() -> None:
    payload = build_run_task_payload(model="qwen-audio-3.0-asr-flash-streaming", task_id="abc")
    assert payload["header"] == {"action": "run-task", "task_id": "abc", "streaming": "duplex"}
    body = payload["payload"]
    assert body["task_group"] == "audio"
    assert body["task"] == "asr"
    assert body["function"] == "recognition"
    assert body["model"] == "qwen-audio-3.0-asr-flash-streaming"
    params = body["parameters"]
    assert params["format"] == "pcm"
    assert params["sample_rate"] == 16000
    assert params["disfluency_removal_enabled"] is True
    assert params["punctuation_prediction_enabled"] is True
    assert params["semantic_punctuation_enabled"] is False
    assert params["heartbeat"] is True
    assert params["language_hints"] == ["zh"]


def test_run_task_clamps_sentence_silence() -> None:
    assert build_run_task_payload(model="m", task_id="t", max_sentence_silence=10)["payload"]["parameters"][
        "max_sentence_silence"
    ] == 200
    assert build_run_task_payload(model="m", task_id="t", max_sentence_silence=99999)["payload"]["parameters"][
        "max_sentence_silence"
    ] == 6000


def test_run_task_respects_disfluency_flag() -> None:
    payload = build_run_task_payload(model="m", task_id="t", disfluency_removal=False)
    assert payload["payload"]["parameters"]["disfluency_removal_enabled"] is False


def test_finish_task_payload_reuses_task_id() -> None:
    payload = build_finish_task_payload("same-id")
    assert payload["header"]["action"] == "finish-task"
    assert payload["header"]["task_id"] == "same-id"
    assert payload["payload"] == {"input": {}}


def test_parse_result_sentence() -> None:
    text, end, begin, finish, heartbeat = parse_result_sentence(_result_event("你好", True, 100, 900))
    assert (text, end, begin, finish, heartbeat) == ("你好", True, 100, 900, False)


def test_parse_result_sentence_handles_missing_fields() -> None:
    assert parse_result_sentence({}) == ("", False, None, None, False)
    assert parse_result_sentence({"payload": {"output": {}}}) == ("", False, None, None, False)


def test_parse_usage_seconds() -> None:
    assert parse_usage_seconds({"payload": {"usage": {"duration": 7}}}) == 7
    assert parse_usage_seconds({"payload": {"usage": None}}) is None
    assert parse_usage_seconds({}) is None


@pytest.mark.asyncio
async def test_session_emits_partial_and_final(monkeypatch) -> None:
    events = [
        {"header": {"event": "task-started"}, "payload": {}},
        _result_event("你好", False),
        _result_event("你好我们", False),
        _result_event("你好，我们明天见。", True, 120, 1800),
        {"header": {"event": "task-finished"}, "payload": {"output": {}}},
    ]
    fake = FakeWebSocket(events)
    partials: list[str] = []
    finals: list[tuple[str, int | None, int | None]] = []

    session = VoiceAsrSession(
        api_key="sk-test",
        model="m",
        on_partial=lambda text: partials.append(text),
        on_final=lambda sentence: finals.append((sentence.text, sentence.begin_ms, sentence.end_ms)),
    )

    async def fake_connect(*_args, **_kwargs):
        return fake

    monkeypatch.setattr("app.services.voice_asr.connect", fake_connect)

    await session.start()
    await session.push_audio(b"\x00\x00" * 1600)
    await session.finish()
    await session.close()

    assert partials == ["你好", "你好我们"]
    assert finals == [("你好，我们明天见。", 120, 1800)]
    assert session.usage_seconds == 3
    assert fake.sent_bytes == [b"\x00\x00" * 1600]
    assert fake.sent_text[0]["header"]["action"] == "run-task"
    assert fake.sent_text[-1]["header"]["action"] == "finish-task"
    assert fake.closed is True


@pytest.mark.asyncio
async def test_session_skips_heartbeat_and_empty_text(monkeypatch) -> None:
    events = [
        {"header": {"event": "task-started"}, "payload": {}},
        {
            "header": {"event": "result-generated"},
            "payload": {"output": {"sentence": {"text": "", "sentence_end": False, "heartbeat": True}}},
        },
        _result_event("", False),
        {"header": {"event": "task-finished"}, "payload": {}},
    ]
    fake = FakeWebSocket(events)
    partials: list[str] = []
    session = VoiceAsrSession(api_key="sk-test", model="m", on_partial=lambda t: partials.append(t))

    async def fake_connect(*_args, **_kwargs):
        return fake

    monkeypatch.setattr("app.services.voice_asr.connect", fake_connect)
    await session.start()
    await session.finish()
    await session.close()
    assert partials == []


@pytest.mark.asyncio
async def test_session_raises_on_task_failed(monkeypatch) -> None:
    events = [
        {
            "header": {"event": "task-failed", "error_code": "CLIENT_ERROR", "error_message": "request timeout"},
            "payload": {},
        }
    ]
    fake = FakeWebSocket(events)
    session = VoiceAsrSession(api_key="sk-test", model="m")

    async def fake_connect(*_args, **_kwargs):
        return fake

    monkeypatch.setattr("app.services.voice_asr.connect", fake_connect)
    with pytest.raises(AsrError) as excinfo:
        await session.start()
    assert excinfo.value.code == "ASR_FAILED"
    assert "request timeout" in str(excinfo.value)
    await session.close()


@pytest.mark.asyncio
async def test_missing_api_key_raises_unavailable() -> None:
    session = VoiceAsrSession(api_key="", model="m")
    with pytest.raises(AsrError) as excinfo:
        await session.start()
    assert excinfo.value.code == "ASR_UNAVAILABLE"


@pytest.mark.asyncio
async def test_connect_failure_is_wrapped(monkeypatch) -> None:
    async def boom(*_args, **_kwargs):
        raise OSError("网络不可达")

    monkeypatch.setattr("app.services.voice_asr.connect", boom)
    session = VoiceAsrSession(api_key="sk-test", model="m")
    with pytest.raises(AsrError) as excinfo:
        await session.start()
    assert excinfo.value.code == "ASR_UNAVAILABLE"
    assert "网络不可达" in (excinfo.value.detail or "")


@pytest.mark.asyncio
async def test_callback_exception_does_not_break_loop(monkeypatch) -> None:
    events = [
        {"header": {"event": "task-started"}, "payload": {}},
        _result_event("你好", False),
        _result_event("你好，世界。", True),
        {"header": {"event": "task-finished"}, "payload": {}},
    ]
    fake = FakeWebSocket(events)
    seen: list[str] = []

    def bad_partial(text: str) -> None:
        raise RuntimeError("回调炸了")

    session = VoiceAsrSession(
        api_key="sk-test",
        model="m",
        on_partial=bad_partial,
        on_final=lambda sentence: seen.append(sentence.text),
    )

    async def fake_connect(*_args, **_kwargs):
        return fake

    monkeypatch.setattr("app.services.voice_asr.connect", fake_connect)
    await session.start()
    await session.finish()
    await session.close()
    assert seen == ["你好，世界。"]


@pytest.mark.asyncio
async def test_close_is_idempotent(monkeypatch) -> None:
    fake = FakeWebSocket([{"header": {"event": "task-started"}, "payload": {}}])

    async def fake_connect(*_args, **_kwargs):
        return fake

    monkeypatch.setattr("app.services.voice_asr.connect", fake_connect)
    session = VoiceAsrSession(api_key="sk-test", model="m")
    await session.start()
    await session.close()
    await session.close()
    assert fake.closed is True

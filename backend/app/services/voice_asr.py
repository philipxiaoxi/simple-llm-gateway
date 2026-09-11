"""阿里云百炼（DashScope）实时语音识别客户端。

协议事实（均已实测，详见 docs/voice-input/协议参考.md）：
- 端点：wss://dashscope.aliyuncs.com/api-ws/v1/inference
- 鉴权：握手头 Authorization: Bearer <sk-ws-…>
- 流程：run-task → task-started → 二进制 PCM 帧 → result-generated(多次) → finish-task → task-finished
- 音频：单声道 PCM16LE，二进制帧直发（不需要 base64）
- 同一端点同一份报文可跑 qwen-audio-3.0-asr-flash-streaming / fun-asr-realtime / paraformer-realtime-v2
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from websockets.asyncio.client import connect

from app.config import get_settings

CONNECT_TIMEOUT_SECONDS = 10.0
TASK_STARTED_TIMEOUT_SECONDS = 15.0
TASK_FINISHED_TIMEOUT_SECONDS = 15.0
FINISH_GRACE_SECONDS = 3.0


class AsrError(Exception):
    """语音识别失败。code 为可上报给客户端的机器可读标识。"""

    def __init__(self, message: str, code: str = "ASR_FAILED", detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.detail = detail


@dataclass(slots=True)
class FinalSentence:
    """一句已经定稿的识别结果。"""

    text: str
    begin_ms: int | None = None
    end_ms: int | None = None


PartialCallback = Callable[[str], Awaitable[None] | None]
FinalCallback = Callable[[FinalSentence], Awaitable[None] | None]


def build_run_task_payload(
    *,
    model: str,
    task_id: str,
    sample_rate: int = 16000,
    audio_format: str = "pcm",
    disfluency_removal: bool = True,
    language_hints: list[str] | None = None,
    max_sentence_silence: int = 700,
) -> dict[str, Any]:
    """构造 run-task 报文。

    disfluency_removal=False 时 ASR 一个语气词都不会删（实测），所以默认开启。
    max_sentence_silence 官方默认 1300ms，语音输入场景调小让句子更快定稿。
    """
    return {
        "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
        "payload": {
            "task_group": "audio",
            "task": "asr",
            "function": "recognition",
            "model": model,
            "parameters": {
                "format": audio_format,
                "sample_rate": sample_rate,
                "disfluency_removal_enabled": bool(disfluency_removal),
                "punctuation_prediction_enabled": True,
                "inverse_text_normalization_enabled": True,
                "semantic_punctuation_enabled": False,
                "max_sentence_silence": max(200, min(6000, int(max_sentence_silence))),
                "heartbeat": True,
                "language_hints": list(language_hints or ["zh"]),
            },
            "input": {},
        },
    }


def build_finish_task_payload(task_id: str) -> dict[str, Any]:
    return {
        "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
        "payload": {"input": {}},
    }


def parse_result_sentence(event: dict[str, Any]) -> tuple[str, bool, int | None, int | None, bool]:
    """从 result-generated 事件里取出 (text, sentence_end, begin_ms, end_ms, is_heartbeat)。"""
    output = (event.get("payload") or {}).get("output") or {}
    sentence = output.get("sentence") or {}
    text = sentence.get("text") or ""
    return (
        text,
        bool(sentence.get("sentence_end")),
        sentence.get("begin_time"),
        sentence.get("end_time"),
        bool(sentence.get("heartbeat")),
    )


def parse_usage_seconds(event: dict[str, Any]) -> int | None:
    usage = (event.get("payload") or {}).get("usage") or {}
    duration = usage.get("duration")
    return int(duration) if isinstance(duration, (int, float)) else None


class VoiceAsrSession:
    """一次录音会话对应一条 DashScope WebSocket 连接。

    用法：
        session = VoiceAsrSession(api_key=..., model=..., on_partial=..., on_final=...)
        await session.start()
        await session.push_audio(pcm)          # 可反复调用
        await session.finish()                 # 发 finish-task，等 task-finished
        await session.close()                  # 释放连接（幂等）
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        on_partial: PartialCallback | None = None,
        on_final: FinalCallback | None = None,
        sample_rate: int = 16000,
        disfluency_removal: bool = True,
        language_hints: list[str] | None = None,
        max_sentence_silence: int = 700,
        ws_url: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.sample_rate = sample_rate
        self.disfluency_removal = disfluency_removal
        self.language_hints = language_hints or ["zh"]
        self.max_sentence_silence = max_sentence_silence
        self.ws_url = ws_url or get_settings().aliyun_asr_ws_url
        self.task_id = str(uuid.uuid4())
        self.usage_seconds: int | None = None
        self.final_count = 0
        self.partial_count = 0
        self._on_partial = on_partial
        self._on_final = on_final
        self._ws: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._finished = asyncio.Event()
        self._failure: AsrError | None = None
        self._last_partial = ""
        self._closed = False
        self._finish_sent = False

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        if not self.api_key:
            raise AsrError("未配置阿里云语音识别 API Key", code="ASR_UNAVAILABLE")
        headers = {"Authorization": f"Bearer {self.api_key}", "user-agent": "llm-gateway-voice/0.1"}
        try:
            self._ws = await asyncio.wait_for(
                connect(self.ws_url, additional_headers=headers, max_size=2**22, ping_interval=20, ping_timeout=20),
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise AsrError("连接语音识别服务超时", code="ASR_UNAVAILABLE", detail=str(exc)) from exc
        except Exception as exc:  # websockets 的异常类型较多，这里统一转成业务错误
            raise AsrError(
                "连接语音识别服务失败", code="ASR_UNAVAILABLE", detail=f"{type(exc).__name__}: {exc}"
            ) from exc

        self._reader = asyncio.create_task(self._read_loop())
        payload = build_run_task_payload(
            model=self.model,
            task_id=self.task_id,
            sample_rate=self.sample_rate,
            disfluency_removal=self.disfluency_removal,
            language_hints=self.language_hints,
            max_sentence_silence=self.max_sentence_silence,
        )
        try:
            await self._ws.send(json.dumps(payload))
        except Exception as exc:
            raise AsrError("发送 run-task 失败", code="ASR_FAILED", detail=str(exc)) from exc

        try:
            await asyncio.wait_for(self._started.wait(), timeout=TASK_STARTED_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise AsrError(
                "语音识别服务未响应 task-started", code="ASR_FAILED", detail=str(exc)
            ) from exc
        if self._failure is not None:
            raise self._failure

    async def push_audio(self, pcm: bytes) -> None:
        """直通发送一帧 PCM，不做任何缓冲或转码。"""
        if self._closed or self._ws is None:
            return
        await self._ws.send(pcm)

    async def finish(self) -> None:
        """发送 finish-task 并等待 task-finished。失败不抛异常，由调用方按已有结果处理。"""
        if self._closed or self._ws is None or self._finish_sent:
            return
        self._finish_sent = True
        try:
            await self._ws.send(json.dumps(build_finish_task_payload(self.task_id)))
        except Exception:
            return
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._finished.wait(), timeout=TASK_FINISHED_TIMEOUT_SECONDS)
        # 给仍在路上的 result-generated 一点时间落地，避免丢掉最后一句
        await asyncio.sleep(0.05)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()

    async def abort(self) -> None:
        await self.close()

    # ---------- 事件循环 ----------

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                if isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    event = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                await self._handle_event(event)
                if self._finished.is_set():
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._finished.is_set():
                self._failure = AsrError(
                    "语音识别连接中断", code="ASR_FAILED", detail=f"{type(exc).__name__}: {exc}"
                )
                self._finished.set()

    async def _handle_event(self, event: dict[str, Any]) -> None:
        header = event.get("header") or {}
        name = header.get("event")
        if name == "task-started":
            self._started.set()
            return
        if name == "task-failed":
            self._failure = AsrError(
                header.get("error_message") or "语音识别任务失败",
                code="ASR_FAILED",
                detail=f"{header.get('error_code')}: {header.get('error_message')}",
            )
            self._started.set()
            self._finished.set()
            return
        if name == "result-generated":
            usage = parse_usage_seconds(event)
            if usage is not None:
                self.usage_seconds = usage
            text, sentence_end, begin_ms, end_ms, heartbeat = parse_result_sentence(event)
            if heartbeat or not text:
                return
            if sentence_end:
                self.final_count += 1
                self._last_partial = ""
                await self._emit(self._on_final, FinalSentence(text=text, begin_ms=begin_ms, end_ms=end_ms))
            else:
                if text == self._last_partial:
                    return
                self._last_partial = text
                self.partial_count += 1
                await self._emit(self._on_partial, text)
            return
        if name == "task-finished":
            self._finished.set()

    @staticmethod
    async def _emit(callback: Any, value: Any) -> None:
        if callback is None:
            return
        try:
            result = callback(value)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            # 回调异常不能打断识别主循环
            return

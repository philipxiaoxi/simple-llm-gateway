from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection


class AliyunAsrError(RuntimeError):
    pass


class AliyunAsrSession:
    """阿里云实时语音识别会话（qwen-audio-3.0-asr-flash-streaming）。

    协议：WebSocket 双向流，先发 run-task 建立任务，收到 task-started 后
    流式发送 PCM（16kHz/16bit/单声道）二进制帧，最后发 finish-task 结束。
    服务端持续回推 result-generated（含中间结果与 sentence_end 最终结果）。
    """

    def __init__(self, api_key: str, *, ws_url: str, model: str) -> None:
        self.api_key = api_key
        self.ws_url = ws_url
        self.model = model
        self.task_id = uuid.uuid4().hex[:32]
        self._ws: ClientConnection | None = None

    async def start(self) -> None:
        self._ws = await websockets.connect(
            self.ws_url,
            additional_headers={"Authorization": f"bearer {self.api_key}"},
            open_timeout=15,
        )
        await self._ws.send(
            json.dumps(
                {
                    "header": {"action": "run-task", "task_id": self.task_id, "streaming": "duplex"},
                    "payload": {
                        "task_group": "audio",
                        "task": "asr",
                        "function": "recognition",
                        "model": self.model,
                        "parameters": {"sample_rate": 16000, "format": "pcm"},
                        "input": {},
                    },
                }
            )
        )
        while True:
            message = await self._ws.recv()
            event = _event_of(message)
            if event == "task-started":
                return
            if event == "task-failed":
                raise AliyunAsrError(_error_of(message) or "阿里云 ASR 任务启动失败")
            if event == "task-finished":
                raise AliyunAsrError("阿里云 ASR 任务提前结束")

    async def send_audio(self, pcm: bytes) -> None:
        if self._ws is None:
            raise AliyunAsrError("ASR 会话尚未建立")
        await self._ws.send(pcm)

    async def finish(self) -> None:
        if self._ws is None:
            return
        await self._ws.send(
            json.dumps(
                {
                    "header": {"action": "finish-task", "task_id": self.task_id, "streaming": "duplex"},
                    "payload": {"input": {}},
                }
            )
        )

    async def results(self) -> AsyncIterator[dict[str, Any]]:
        """迭代识别结果，每个元素为 {text, sentence_end}。"""
        if self._ws is None:
            return
        while True:
            raw = await self._ws.recv()
            message = _parse_message(raw)
            event = str(message.get("header", {}).get("event") or "")
            if event == "result-generated":
                sentence = message.get("payload", {}).get("output", {}).get("sentence", {})
                yield {
                    "text": str(sentence.get("text") or ""),
                    "sentence_end": bool(sentence.get("sentence_end", False)),
                }
            elif event == "task-finished":
                return
            elif event == "task-failed":
                raise AliyunAsrError(_error_of(message) or "阿里云 ASR 识别失败")

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None


def _parse_message(message: Any) -> dict[str, Any]:
    if isinstance(message, (bytes, bytearray)):
        message = json.loads(bytes(message).decode("utf-8"))
    if isinstance(message, str):
        message = json.loads(message)
    return message if isinstance(message, dict) else {}


def _event_of(message: Any) -> str:
    return str(_parse_message(message).get("header", {}).get("event") or "")


def _error_of(message: Any) -> str | None:
    if not isinstance(message, dict):
        return None
    header = message.get("header") or {}
    return header.get("error_message") or header.get("error_code")

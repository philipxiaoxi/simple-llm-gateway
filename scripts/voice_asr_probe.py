#!/usr/bin/env python3
"""阿里云百炼（DashScope）实时语音识别连通性自检脚本。

用途：在开发/部署语音输入功能前后，快速确认 Key、端点、模型、报文格式是否仍然可用。
不依赖项目代码，只需要 websockets（已在 .venv 中）。

用法：
    cd /Users/philip/code/llm-gateway
    ALIYUN_DASHSCOPE_API_KEY=sk-ws-xxx ./.venv/bin/python scripts/voice_asr_probe.py
    ALIYUN_DASHSCOPE_API_KEY=sk-ws-xxx ./.venv/bin/python scripts/voice_asr_probe.py --audio some.wav

默认发送 800ms 静音（PCM16LE / 16kHz / 单声道），只验证握手与事件链路，不产生真实识别内容。
传入 --audio 时读取一个 16k 单声道 PCM/WAV 文件并发送，用于验证真实识别效果。

退出码：0 全部模型通过；1 有模型失败。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

try:
    from websockets.asyncio.client import connect
except ImportError:  # pragma: no cover - 提示装依赖
    print("缺少 websockets，请先 pip install websockets==17.0.1", file=sys.stderr)
    raise SystemExit(2)

DEFAULT_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
DEFAULT_MODELS = ("qwen-audio-3.0-asr-flash-streaming", "fun-asr-realtime", "paraformer-realtime-v2")
SILENCE_FRAME_MS = 100
SILENCE_FRAMES = 8


def build_task_payload(model: str, sample_rate: int, language: str) -> dict:
    """run-task 报文，字段含义见 docs/voice-input/协议参考.md §1.3。"""
    return {
        "header": {
            "action": "run-task",
            "task_id": str(uuid.uuid4()),
            "streaming": "duplex",
        },
        "payload": {
            "task_group": "audio",
            "task": "asr",
            "function": "recognition",
            "model": model,
            "parameters": {
                "format": "pcm",
                "sample_rate": sample_rate,
                "disfluency_removal_enabled": False,
                "punctuation_prediction_enabled": True,
                "inverse_text_normalization_enabled": True,
                "semantic_punctuation_enabled": False,
                "max_sentence_silence": 700,
                "heartbeat": True,
                "language_hints": [language],
            },
            "input": {},
        },
    }


def load_audio(path: str | None, sample_rate: int) -> tuple[bytes, int]:
    """返回 (pcm_bytes, frame_ms)。WAV 会跳过 44 字节头并按 sample_rate 校验。"""
    if path is None:
        frame = b"\x00\x00" * (sample_rate // 10)  # 100ms 静音
        return frame * SILENCE_FRAMES, SILENCE_FRAME_MS
    raw = open(path, "rb").read()
    if raw[:4] == b"RIFF":
        channels = int.from_bytes(raw[22:24], "little")
        rate = int.from_bytes(raw[24:28], "little")
        bits = int.from_bytes(raw[34:36], "little")
        if channels != 1 or rate != sample_rate or bits != 16:
            print(f"警告：WAV 期望 单声道/{sample_rate}Hz/16bit，实际 {channels}ch/{rate}Hz/{bits}bit", file=sys.stderr)
        raw = raw[44:]
    return raw, SILENCE_FRAME_MS


async def probe(api_key: str, url: str, model: str, audio: bytes, sample_rate: int, language: str) -> bool:
    payload = build_task_payload(model, sample_rate, language)
    task_id = payload["header"]["task_id"]
    frame_bytes = sample_rate // 10 * 2  # 100ms
    finals: list[str] = []
    partials = 0
    ok = False

    print(f"=== {model} ===")
    try:
        async with connect(
            url,
            additional_headers={"Authorization": f"Bearer {api_key}", "user-agent": "llm-gateway-voice-probe/0.1"},
            max_size=2**22,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        ) as ws:
            print("  ✓ 握手成功（WSS 101）")
            await ws.send(json.dumps(payload))

            started = asyncio.Event()
            finished = asyncio.Event()

            async def reader() -> None:
                nonlocal partials
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    event = json.loads(raw)
                    name = event.get("header", {}).get("event")
                    if name == "task-started":
                        print("  ✓ task-started")
                        started.set()
                    elif name == "result-generated":
                        sentence = (event.get("payload", {}).get("output") or {}).get("sentence") or {}
                        text = sentence.get("text") or ""
                        if sentence.get("heartbeat"):
                            continue
                        if sentence.get("sentence_end"):
                            if text:
                                finals.append(text)
                                print(f"  ✓ 终稿：{text}")
                        elif text:
                            partials += 1
                    elif name == "task-finished":
                        usage = (event.get("payload") or {}).get("usage") or {}
                        print(f"  ✓ task-finished（计费时长 {usage.get('duration', '?')}s）")
                        finished.set()
                        return
                    elif name == "task-failed":
                        header = event.get("header", {})
                        print(f"  ✗ task-failed: {header.get('error_code')} {header.get('error_message')}")
                        finished.set()
                        return

            reader_task = asyncio.create_task(reader())
            try:
                await asyncio.wait_for(started.wait(), timeout=15)
            except TimeoutError:
                print("  ✗ 15s 内未收到 task-started")
                reader_task.cancel()
                return False

            for offset in range(0, len(audio), frame_bytes):
                await ws.send(audio[offset : offset + frame_bytes])
                await asyncio.sleep(SILENCE_FRAME_MS / 1000)
            print(f"  ✓ 已发送音频 {len(audio)} 字节（约 {len(audio) / (sample_rate * 2) * 1000:.0f}ms）")

            await ws.send(
                json.dumps(
                    {"header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"}, "payload": {"input": {}}}
                )
            )
            try:
                await asyncio.wait_for(finished.wait(), timeout=15)
                ok = finished.is_set() and not reader_task.cancelled()
            except TimeoutError:
                print("  ✗ 15s 内未收到 task-finished")
                ok = False
            finally:
                reader_task.cancel()
    except Exception as exc:  # noqa: BLE001 - 自检脚本，展示所有异常
        print(f"  ✗ 异常：{type(exc).__name__}: {exc}")
        return False

    if partials:
        print(f"  ℹ 收到 {partials} 条中间结果")
    if not finals and audio and any(audio):
        print("  ℹ 未产生终稿（音频可能过短或为静音）")
    print()
    return ok


async def main() -> int:
    parser = argparse.ArgumentParser(description="DashScope 实时语音识别连通性自检")
    parser.add_argument("--url", default=os.environ.get("ALIYUN_ASR_WS_URL", DEFAULT_URL))
    parser.add_argument(
        "--models",
        default=os.environ.get("ALIYUN_ASR_MODELS", ",".join(DEFAULT_MODELS)),
        help="逗号分隔的模型列表",
    )
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--audio", default=None, help="16k 单声道 PCM/WAV 文件；不传则发静音")
    args = parser.parse_args()

    api_key = os.environ.get("ALIYUN_DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        print("请先设置 ALIYUN_DASHSCOPE_API_KEY", file=sys.stderr)
        return 2

    audio, _ = load_audio(args.audio, args.sample_rate)
    results = []
    for model in [item.strip() for item in args.models.split(",") if item.strip()]:
        results.append(await probe(api_key, args.url, model, audio, args.sample_rate, args.language))

    print(f"结果：{sum(results)}/{len(results)} 个模型通过")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import wave
from io import BytesIO
from typing import Any

import httpx

from app.capabilities.base import CallContext, CapabilityError, CapabilitySpec, McpToolDef
from app.capabilities.deepseek_web import BASE_URL, bound_credential

CAPABILITY_ID = "deepseek_asr"
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_BASE64_CHARS = 4 * ((MAX_AUDIO_BYTES + 2) // 3)
MAX_JSON_BYTES = MAX_BASE64_CHARS + 1024
MAX_RESPONSE_BYTES = 256 * 1024
SAMPLE_RATES = {8000, 16000, 22050, 24000, 32000, 44100, 48000}
INPUT_SCHEMA = {
    "type": "object",
    "properties": {"audio_base64": {"type": "string", "minLength": 1, "maxLength": MAX_BASE64_CHARS,
                                    "description": "完整 WAV 或 Ogg Opus 文件的标准 Base64，不带 data: 前缀"}},
    "required": ["audio_base64"],
    "additionalProperties": False,
}


def decode_audio(payload: dict[str, Any]) -> tuple[bytes, str, str]:
    value = payload.get("audio_base64")
    if set(payload) != {"audio_base64"} or not isinstance(value, str) or not value:
        raise CapabilityError("请提供 audio_base64 音频文件", 400, "invalid_request")
    if len(value) > MAX_BASE64_CHARS:
        raise CapabilityError("音频文件不能超过 8 MiB", 413, "invalid_request")
    try:
        audio = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise CapabilityError("音频必须使用标准 Base64，不带 data: 前缀", 400, "invalid_request") from None
    if len(audio) > MAX_AUDIO_BYTES:
        raise CapabilityError("音频文件不能超过 8 MiB", 413, "invalid_request")
    if audio.startswith(b"RIFF") and audio[8:12] == b"WAVE":
        try:
            with wave.open(BytesIO(audio), "rb") as source:
                rate = source.getframerate()
                if (source.getsampwidth() == 2 and source.getnchannels() in {1, 2} and rate in SAMPLE_RATES
                        and 0 < source.getnframes() <= rate * 120):
                    return audio, "speech.wav", "audio/wav"
        except (wave.Error, EOFError):
            pass
    if audio.startswith(b"OggS") and b"OpusHead" in audio[27:64]:
        # DeepSeek validates the stream and its duration for Opus.
        return audio, "speech.ogg", "audio/ogg"
    raise CapabilityError("仅支持最长 120 秒的 16 位 PCM WAV 或 Ogg Opus", 400, "invalid_request")


async def transcribe(credential: str, audio: bytes, filename: str, content_type: str) -> dict:
    try:
        async with (
            asyncio.timeout(90),
            httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), follow_redirects=False) as client,
            client.stream("POST", f"{BASE_URL}/asr", headers={"Authorization": f"Bearer {credential}"},
                          files={"file": (filename, audio, content_type)}) as response,
        ):
            if response.status_code != 200:
                status, message = {
                    400: (400, "DeepSeek 拒绝了音频格式或时长"), 402: (402, "DeepSeek 上游余额不足"),
                    413: (413, "DeepSeek 拒绝了超限的音频"), 429: (429, "DeepSeek 语音识别请求过多"),
                    503: (503, "DeepSeek 语音识别服务繁忙"), 504: (504, "DeepSeek 语音识别超时"),
                }.get(response.status_code, (502, "DeepSeek 语音识别上游请求失败"))
                raise CapabilityError(message, status, "upstream_error")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise CapabilityError("DeepSeek 语音识别响应过大", 502, "upstream_error")
                content.extend(chunk)
        result = json.loads(content)
        if (not isinstance(result, dict) or not isinstance(result.get("request_id"), str)
                or not isinstance(result.get("text"), str) or type(result.get("duration_ms")) is not int):
            raise ValueError
        return {field: result[field] for field in ("request_id", "text", "duration_ms")}
    except (httpx.TimeoutException, TimeoutError):
        raise CapabilityError("DeepSeek 语音识别超时", 504, "upstream_error") from None
    except (httpx.HTTPError, ValueError, RecursionError):
        raise CapabilityError("DeepSeek 语音识别上游连接或响应无效", 502, "upstream_error") from None


class DeepSeekAsrProvider:
    spec = CapabilitySpec(
        capability_id=CAPABILITY_ID, name="DeepSeek 语音识别",
        description="将完整 WAV 或 Ogg Opus 音频转写为文字，不支持流式识别。",
        category="audio", admin_path="/mcp-plaza/deepseek-asr", input_schema={"transcribe": INPUT_SCHEMA},
        integration={
            "rest_endpoints": [{"method": "POST", "path": "/v1/capabilities/deepseek_asr/transcribe",
                                "summary": "将 audio_base64 音频转写为文字", "content_type": "application/json"}],
            "notes": ["将完整音频文件编码为标准 Base64，填写 audio_base64，不带 data: 前缀。",
                      "仅支持 16 位 PCM WAV 或 Ogg Opus，最大 8 MiB、最长 120 秒；双声道只识别左声道。",
                      "自动识别语种，不支持流式识别。音频发送至 DeepSeek，按上游音频时长计费。"],
        },
    )

    def list_mcp_tools(self) -> list[McpToolDef]:
        return [McpToolDef("deepseek_asr", "转写完整音频文件为文字（WAV 或 Ogg Opus，最大 8 MiB、最长 120 秒）。",
                           INPUT_SCHEMA, "transcribe", "request_id、text（转写文本）、duration_ms（音频时长）")]

    async def dispatch(self, operation: str, payload: dict[str, Any], ctx: CallContext) -> dict:
        if operation != "transcribe":
            raise CapabilityError("不支持的语音识别操作", 404, "not_found")
        audio, filename, content_type = decode_audio(payload)
        return await transcribe(bound_credential(ctx), audio, filename, content_type)

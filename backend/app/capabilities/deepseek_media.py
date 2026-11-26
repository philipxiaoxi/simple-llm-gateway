from __future__ import annotations

import asyncio
import base64
import json
import struct
from pathlib import PurePosixPath
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.capabilities.base import CallContext, CapabilityError, CapabilitySpec, McpToolDef
from app.capabilities.deepseek_asr import MAX_AUDIO_BYTES, MAX_BASE64_CHARS
from app.capabilities.deepseek_web import BASE_URL, bound_credential

MEDIA_CAPABILITIES = frozenset({"deepseek_asr", "deepseek_tts", "deepseek_ocr"})
DOCUMENT_SUFFIXES = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
STREAMING_WAV_SIZE = 0xFFFFFFFF


def finalize_wav(audio: bytes) -> bytes:
    """Resolve RIFF/data size sentinels after a successful, complete HTTP response."""
    invalid = CapabilityError("DeepSeek 返回的 WAV 文件无效或不完整", 502, "upstream_error")
    if not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
        raise invalid
    riff_size = struct.unpack_from("<I", audio, 4)[0]
    if riff_size not in {len(audio) - 8, STREAMING_WAV_SIZE}:
        raise invalid
    cursor = 12
    found_data = False
    found_format = False
    patched_data = None
    while cursor + 8 <= len(audio):
        chunk = audio[cursor:cursor + 4]
        size = struct.unpack_from("<I", audio, cursor + 4)[0]
        remaining = len(audio) - cursor - 8
        if chunk == b"data":
            if found_data or not found_format or not size:
                raise invalid
            found_data = True
            if size == STREAMING_WAV_SIZE:
                # DeepSeek streams 16-bit PCM as the final chunk, with no trailing metadata.
                if not remaining or remaining % 2:
                    raise invalid
                size = remaining
                patched_data = (cursor + 4, size)
        if size + size % 2 > remaining:
            raise invalid
        if chunk == b"fmt " and size >= 16:
            found_format = True
        cursor += 8 + size + size % 2
    if not found_data or cursor != len(audio):
        raise invalid
    if riff_size != STREAMING_WAV_SIZE and patched_data is None:
        return audio
    output = bytearray(audio)
    struct.pack_into("<I", output, 4, len(audio) - 8)
    if patched_data is not None:
        struct.pack_into("<I", output, *patched_data)
    return bytes(output)


class TtsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: Annotated[str, Field(min_length=1, max_length=10_000, description="待朗读文本，最多 10,000 个计费字符")]
    voice_id: Literal["stella", "tide"] = "stella"
    format: Literal["wav", "pcm"] = "wav"
    filter_markdown: bool = True


class OcrInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_base64: Annotated[str, Field(min_length=1, max_length=MAX_BASE64_CHARS, description="文件的标准 Base64，不带 data: 前缀")]
    filename: Annotated[str, Field(min_length=1, max_length=200, description="含扩展名的文件名，如 document.pdf")]
    purpose: Literal["text_recognition", "rich_layout_recognition"] = "text_recognition"


async def upstream(operation: str, credential: str, limit: int, seconds: int, **kwargs) -> bytes:
    try:
        async with (
            asyncio.timeout(seconds),
            httpx.AsyncClient(timeout=httpx.Timeout(seconds, connect=10), follow_redirects=False) as client,
            client.stream("POST", f"{BASE_URL}/{operation}", headers={"Authorization": f"Bearer {credential}"}, **kwargs) as response,
        ):
            if response.status_code != 200:
                status = response.status_code
                messages = {400: "DeepSeek 拒绝了参数或文件", 402: "DeepSeek 上游余额不足", 413: "DeepSeek 拒绝了超限的文件",
                            429: "DeepSeek 请求过多", 503: "DeepSeek 服务繁忙", 504: "DeepSeek 请求超时"}
                raise CapabilityError(messages.get(status, "DeepSeek 上游请求失败"), status if status in messages else 502, "upstream_error")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > limit:
                    raise CapabilityError("DeepSeek 返回内容超过平台限制", 502, "upstream_error")
                content.extend(chunk)
            return bytes(content)
    except (TimeoutError, httpx.TimeoutException):
        raise CapabilityError("DeepSeek 请求超时", 504, "upstream_error") from None
    except httpx.HTTPError:
        raise CapabilityError("DeepSeek 上游连接失败或响应中断", 502, "upstream_error") from None


def decode_ocr(arguments: OcrInput) -> tuple[str, bytes]:
    filename = arguments.filename.strip().replace("\\", "/").rsplit("/", 1)[-1]
    if PurePosixPath(filename).suffix.lower() not in DOCUMENT_SUFFIXES or any(ord(c) < 32 for c in filename):
        raise CapabilityError("请提供支持的文档或图片文件名", 400, "invalid_request")
    try:
        content = base64.b64decode(arguments.file_base64, validate=True)
    except ValueError:
        raise CapabilityError("文件必须使用标准 Base64，不带 data: 前缀", 400, "invalid_request") from None
    if len(content) > MAX_AUDIO_BYTES:
        raise CapabilityError("平台的 OCR 文件不能超过 8 MiB", 413, "invalid_request")
    return filename, content


class DeepSeekMediaProvider:
    def __init__(self, operation: Literal["tts", "ocr"]) -> None:
        self.operation = operation
        self.model = TtsInput if operation == "tts" else OcrInput
        self.schema = self.model.model_json_schema()
        title = "DeepSeek 语音合成" if operation == "tts" else "DeepSeek OCR"
        description = "将整段文字合成为 WAV 或 PCM 音频。" if operation == "tts" else "识别文档和图片中的文字，按页返回结果。"
        name = f"deepseek_{operation}"
        endpoint = {"method": "POST", "path": f"/v1/capabilities/{name}/{operation}", "summary": description, "content_type": "application/json"}
        if operation == "tts":
            endpoint["example_body"] = {"text": "你好，我是 DeepSeek。"}
        self.spec = CapabilitySpec(capability_id=name, name=title, description=description,
            category="audio" if operation == "tts" else "document", admin_path=f"/mcp-plaza/deepseek-{operation}",
            input_schema={operation: self.schema}, integration={"rest_endpoints": [endpoint], "notes": [
                "整段合成，返回 audio_base64；PCM 为 24 kHz、16 位单声道。音频输出上限 8 MiB。" if operation == "tts"
                else "平台文件上限 8 MiB；支持 PDF、Word、PowerPoint 和常见图片。按页返回文本及布局标记。",
                "输入会发送至 DeepSeek，按官方规则计费。",
            ]})

    def list_mcp_tools(self) -> list[McpToolDef]:
        return [McpToolDef(self.spec.capability_id, self.spec.description, self.schema, self.operation,
            "audio_base64、format、content_type" if self.operation == "tts" else "id、page_count、pages、usage")]

    async def dispatch(self, operation: str, payload: dict, ctx: CallContext) -> dict:
        if operation != self.operation:
            raise CapabilityError("不支持的 DeepSeek 工具操作", 404, "not_found")
        try:
            args = self.model.model_validate(payload)
        except ValidationError:
            raise CapabilityError("参数无效或超出平台大小限制", 400, "invalid_request") from None
        if isinstance(args, TtsInput):
            body = args.model_dump()
            if len(json.dumps(body, ensure_ascii=False).encode()) > 65536:
                raise CapabilityError("文字请求体不能超过 64 KiB", 413, "invalid_request")
            audio = await upstream(operation, bound_credential(ctx), MAX_AUDIO_BYTES, 90, json=body)
            if args.format == "wav":
                audio = finalize_wav(audio)
            elif not audio or len(audio) % 2:
                raise CapabilityError("DeepSeek 返回的音频无效", 502, "upstream_error")
            return {"audio_base64": base64.b64encode(audio).decode("ascii"), "format": args.format,
                    "content_type": "audio/wav" if args.format == "wav" else "application/octet-stream"}
        filename, content = decode_ocr(args)
        raw = await upstream(operation, bound_credential(ctx), 2 * 1024 * 1024, 185,
                             files={"file": (filename, content)}, data={"purpose": args.purpose})
        try:
            result = json.loads(raw)
        except (ValueError, RecursionError):
            result = None
        if isinstance(result, dict) and isinstance(result.get("error"), dict):
            timeout = result["error"].get("code") == "gateway_timeout_error"
            raise CapabilityError("DeepSeek OCR 识别超时" if timeout else "DeepSeek OCR 识别失败", 504 if timeout else 502, "upstream_error")
        if not isinstance(result, dict) or not isinstance(result.get("pages"), list):
            raise CapabilityError("DeepSeek OCR 响应格式无效", 502, "upstream_error")
        return {field: result[field] for field in ("id", "purpose", "page_count", "pages", "usage") if field in result}

import base64
import json
import struct
import wave
from io import BytesIO

import httpx
import pytest

from app.capabilities.base import CapabilityError
from app.capabilities.deepseek_media import finalize_wav


def test_finalize_wav_rejects_truncated_fixed_length_audio():
    buffer = BytesIO()
    with wave.open(buffer, "wb") as source:
        source.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        source.writeframes(bytes(48))
    audio = buffer.getvalue()
    assert finalize_wav(audio) == audio
    with pytest.raises(CapabilityError, match="无效或不完整"):
        finalize_wav(audio[:-2])


def test_deepseek_media_tools(client, auth_headers, monkeypatch):
    account = client.post("/api/admin/accounts", headers=auth_headers, json={
        "name": "DeepSeek", "provider": "deepseek", "api_key": "fixture-secret",
    }).json()
    assert client.put("/api/admin/mcp/deepseek-web/settings", headers=auth_headers,
                      json={"account_id": account["id"]}).is_success
    key = client.post("/api/admin/mcp/keys", headers=auth_headers, json={
        "name": "media", "capability_ids": ["deepseek_asr", "deepseek_tts", "deepseek_ocr"],
    }).json()
    headers = {"Authorization": f"Bearer {key['key']}", "Accept": "application/json, text/event-stream"}
    buffer = BytesIO()
    with wave.open(buffer, "wb") as source:
        source.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        source.writeframes(bytes(320))
    wav = buffer.getvalue()
    encoded = base64.b64encode(wav).decode()
    outcomes = []

    def upstream(request):
        assert request.headers["Authorization"] == "Bearer fixture-secret"
        operation = request.url.path.rsplit("/", 1)[-1]
        outcomes.append(operation)
        if operation == "asr":
            assert b'name="file"' in request.content and wav in request.content
            return httpx.Response(200, json={"request_id": "demo", "text": "hello", "duration_ms": 10})
        if operation == "tts":
            assert json.loads(request.content)["voice_id"] == "stella"
            streaming_wav = bytearray(wav)
            struct.pack_into("<I", streaming_wav, 4, 0xFFFFFFFF)
            struct.pack_into("<I", streaming_wav, 40, 0xFFFFFFFF)
            return httpx.Response(200, content=bytes(streaming_wav))
        assert b'name="purpose"' in request.content and b"text_recognition" in request.content
        if b"bad.pdf" in request.content:
            return httpx.Response(200, json={"error": {"code": "gateway_timeout_error"}})
        return httpx.Response(200, content=b'\n\n{"page_count":1,"pages":[{"index":0,"text":"demo","finish_reason":"stop"}]}')

    async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: async_client(transport=httpx.MockTransport(upstream), **kwargs))

    def mcp(method, params):
        response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        return response.json()["result"]

    assert {item["name"] for item in mcp("tools/list", {})["tools"]} == {"deepseek_asr", "deepseek_tts", "deepseek_ocr"}
    result = mcp("tools/call", {"name": "deepseek_asr", "arguments": {"audio_base64": encoded}})
    assert json.loads(result["content"][0]["text"])["text"] == "hello"
    invalid = mcp("tools/call", {"name": "deepseek_asr", "arguments": {"audio_base64": "bad"}})
    assert invalid["isError"] is True
    tts = client.post("/v1/capabilities/deepseek_tts/tts", headers=headers, json={"text": "你好"})
    assert tts.json()["audio_base64"] == encoded
    ocr = "/v1/capabilities/deepseek_ocr/ocr"
    assert client.post(ocr, headers=headers, json={"filename": "ok.pdf", "file_base64": "cGRm"}).json()["pages"][0]["text"] == "demo"
    assert client.post(ocr, headers=headers, json={"filename": "bad.pdf", "file_base64": "cGRm"}).status_code == 504
    assert outcomes == ["asr", "tts", "ocr", "ocr"]

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.models import UpstreamAccount, VoiceLog, VoiceMessage, VoiceRoom, VoiceSettings
from app.providers import get_provider
from app.services.credentials import get_upstream_credential

VOICE_ROOM_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
VOICE_ROOM_CODE_LENGTH = 6

DEFAULT_LLM_PROMPT = (
    "你是文字整理助手。下面是一段语音转写文本，请把它整理成通顺、简洁、口语自然的文字，"
    "纠正错别字和明显的语病，去掉重复的废话和口头禅，保留说话人的原意和语气，不要添加没有说过的新内容。"
    "直接输出整理后的文字，不要任何解释、前缀或引号。"
)


def generate_room_code(db: Session) -> str:
    existing: set[str] = set(db.scalars(select(VoiceRoom.code)).all())
    for _ in range(64):
        code = "".join(secrets.choice(VOICE_ROOM_CODE_ALPHABET) for _ in range(VOICE_ROOM_CODE_LENGTH))
        if code not in existing:
            return code
    raise RuntimeError("无法生成唯一房间码，请稍后重试")


@dataclass
class VoiceAccount:
    id: int | None = None
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    source: str = "upstream"
    header_spoof: str = "none"

    @property
    def ready(self) -> bool:
        return bool(self.base_url and self.api_key)


@dataclass
class VoiceConfig:
    stt_account: VoiceAccount = field(default_factory=VoiceAccount)
    stt_model: str = "whisper-1"
    stt_language: str | None = None
    llm_account: VoiceAccount = field(default_factory=VoiceAccount)
    llm_model: str = "gpt-4o-mini"
    llm_prompt: str = ""

    @property
    def stt_ready(self) -> bool:
        return self.stt_account.ready and bool(self.stt_model)

    @property
    def llm_ready(self) -> bool:
        return self.llm_account.ready and bool(self.llm_model)


def _account_voice_view(db: Session, account_id: int | None) -> VoiceAccount:
    if account_id is None:
        return VoiceAccount()
    account = db.get(UpstreamAccount, account_id)
    if account is None or account.status != "active" or account.source == "agent":
        return VoiceAccount(id=account_id, name=account.name if account else "")
    credential = get_upstream_credential(account, allow_expired=True)
    return VoiceAccount(
        id=account.id,
        name=account.name,
        base_url=account.base_url.rstrip("/"),
        api_key=credential or "",
        source=account.source,
        header_spoof=account.header_spoof or "none",
    )


def load_voice_config(db: Session) -> VoiceConfig:
    env = get_settings()
    row = db.scalar(select(VoiceSettings).order_by(VoiceSettings.id).limit(1))
    if row is None:
        stt_account = VoiceAccount(
            base_url=env.voice_stt_base_url.rstrip("/"),
            api_key=env.voice_stt_api_key,
        )
        llm_account = VoiceAccount(
            base_url=env.voice_llm_base_url.rstrip("/"),
            api_key=env.voice_llm_api_key,
        )
        return VoiceConfig(
            stt_account=stt_account,
            stt_model=env.voice_stt_model,
            stt_language=env.voice_stt_language or None,
            llm_account=llm_account,
            llm_model=env.voice_llm_model,
            llm_prompt=env.voice_llm_prompt or DEFAULT_LLM_PROMPT,
        )
    return VoiceConfig(
        stt_account=_account_voice_view(db, row.stt_account_id),
        stt_model=row.stt_model or env.voice_stt_model,
        stt_language=row.stt_language or env.voice_stt_language or None,
        llm_account=_account_voice_view(db, row.llm_account_id),
        llm_model=row.llm_model or env.voice_llm_model,
        llm_prompt=row.llm_prompt or env.voice_llm_prompt or DEFAULT_LLM_PROMPT,
    )


def save_voice_config(
    db: Session,
    *,
    stt_account_id: int | None = None,
    stt_model: str = "",
    stt_language: str | None = None,
    llm_account_id: int | None = None,
    llm_model: str = "",
    llm_prompt: str = "",
) -> VoiceSettings:
    row = db.scalar(select(VoiceSettings).order_by(VoiceSettings.id).limit(1))
    if row is None:
        row = VoiceSettings()
        db.add(row)
    if stt_account_id is not None:
        if stt_account_id <= 0:
            row.stt_account_id = None
        else:
            account = db.get(UpstreamAccount, stt_account_id)
            if account is None:
                raise ValueError("STT 上游账号不存在")
            row.stt_account_id = account.id
    if stt_model != "":
        row.stt_model = stt_model.strip() or "whisper-1"
    if stt_language is not None:
        row.stt_language = stt_language.strip() or None
    if llm_account_id is not None:
        if llm_account_id <= 0:
            row.llm_account_id = None
        else:
            account = db.get(UpstreamAccount, llm_account_id)
            if account is None:
                raise ValueError("LLM 上游账号不存在")
            row.llm_account_id = account.id
    if llm_model != "":
        row.llm_model = llm_model.strip() or "gpt-4o-mini"
    if llm_prompt != "":
        row.llm_prompt = llm_prompt.strip() or DEFAULT_LLM_PROMPT
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def settings_payload(db: Session) -> dict[str, Any]:
    env = get_settings()
    row = db.scalar(select(VoiceSettings).order_by(VoiceSettings.id).limit(1))
    stt_account_id = row.stt_account_id if row else None
    llm_account_id = row.llm_account_id if row else None
    stt_view = _account_voice_view(db, stt_account_id)
    llm_view = _account_voice_view(db, llm_account_id)
    return {
        "stt_account_id": stt_account_id,
        "stt_account_name": stt_view.name,
        "stt_model": (row.stt_model if row and row.stt_model else env.voice_stt_model) or "whisper-1",
        "stt_language": (row.stt_language if row and row.stt_language else env.voice_stt_language) or None,
        "stt_configured": stt_view.ready,
        "llm_account_id": llm_account_id,
        "llm_account_name": llm_view.name,
        "llm_model": (row.llm_model if row and row.llm_model else env.voice_llm_model) or "gpt-4o-mini",
        "llm_configured": llm_view.ready,
        "llm_prompt": (row.llm_prompt if row and row.llm_prompt else env.voice_llm_prompt) or DEFAULT_LLM_PROMPT,
        "stt_accounts": _list_account_options(db),
        "llm_accounts": _list_account_options(db),
    }


def _list_account_options(db: Session) -> list[dict[str, Any]]:
    accounts = db.scalars(
        select(UpstreamAccount)
        .where(UpstreamAccount.status == "active", UpstreamAccount.source == "upstream")
        .order_by(UpstreamAccount.name)
    ).all()
    return [
        {
            "id": account.id,
            "name": account.name,
            "provider": account.provider,
            "base_url": account.base_url,
            "has_credential": bool(get_upstream_credential(account, allow_expired=True)),
        }
        for account in accounts
    ]


async def transcribe_audio(
    db: Session,
    room: VoiceRoom,
    audio_bytes: bytes,
    filename: str = "audio",
) -> dict[str, Any]:
    """把录音转成文字，再用大模型优化表达。

    返回 dict：raw_text / text / stt_model / stt_latency_ms / llm_model / llm_latency_ms。
    """
    config = load_voice_config(db)
    if not config.stt_ready:
        raise RuntimeError("语音识别未配置，请到管理后台「语音输入」→「语音配置」选择上游账号")
    stt_started = time.perf_counter()
    raw_text = await _stt_to_text(config, audio_bytes, filename)
    stt_latency_ms = int((time.perf_counter() - stt_started) * 1000)
    text = raw_text
    llm_latency_ms: int | None = None
    if config.llm_ready and raw_text.strip():
        llm_started = time.perf_counter()
        text = await _polish_text(config, raw_text)
        llm_latency_ms = int((time.perf_counter() - llm_started) * 1000)
    return {
        "raw_text": raw_text,
        "text": text,
        "stt_model": config.stt_model,
        "stt_latency_ms": stt_latency_ms,
        "llm_model": config.llm_model if config.llm_ready else "",
        "llm_latency_ms": llm_latency_ms,
    }


def _headers_for(account: VoiceAccount) -> dict[str, str]:
    headers: dict[str, str] = {}
    if account.api_key:
        headers["Authorization"] = f"Bearer {account.api_key}"
    if account.header_spoof and account.header_spoof != "none":
        from app.services.header_spoof import spoof_headers

        headers.update(spoof_headers(account.header_spoof, model=None))
    return headers


async def _stt_to_text(config: VoiceConfig, audio_bytes: bytes, filename: str) -> str:
    url = _join_url(config.stt_account.base_url, "/audio/transcriptions")
    payload: dict[str, str] = {"model": config.stt_model, "response_format": "json"}
    if config.stt_language:
        payload["language"] = config.stt_language
    headers = _headers_for(config.stt_account)
    timeout = get_settings().voice_http_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            headers=headers,
            data=payload,
            files={"file": (filename, audio_bytes, "audio/webm")},
        )
    if response.status_code >= 400:
        detail = response.text[:300]
        raise RuntimeError(f"语音识别服务返回 {response.status_code}：{detail}")
    body = response.json()
    text = str(body.get("text") or "").strip()
    if not text:
        raise RuntimeError("语音识别未返回文本，请重试或检查音频质量")
    return text


async def _polish_text(config: VoiceConfig, raw_text: str) -> str:
    url = _join_url(config.llm_account.base_url, "/chat/completions")
    headers = {
        **_headers_for(config.llm_account),
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.llm_model,
        "messages": [
            {"role": "system", "content": config.llm_prompt or DEFAULT_LLM_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        "temperature": 0.3,
    }
    timeout = get_settings().voice_http_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=payload)
    if response.status_code >= 400:
        detail = response.text[:300]
        raise RuntimeError(f"大模型优化服务返回 {response.status_code}：{detail}")
    body = response.json()
    choices = body.get("choices") or []
    if not choices:
        return raw_text
    content = (choices[0].get("message") or {}).get("content")
    text = str(content or "").strip()
    return text or raw_text


def _join_url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}{path}"


@dataclass
class VoiceRoomHub:
    """房间级 WebSocket 会话，把转写结果推送给房间内所有连接（桌面端小程序）。"""

    _rooms: dict[str, set[WebSocket]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    async def connect(self, room_code: str, websocket: WebSocket) -> None:
        await websocket.accept()
        with self._lock:
            self._rooms.setdefault(room_code.upper(), set()).add(websocket)
        await websocket.send_json({"type": "connected", "room": room_code.upper()})

    async def disconnect(self, room_code: str, websocket: WebSocket) -> None:
        with self._lock:
            conns = self._rooms.get(room_code.upper())
            if conns is not None:
                conns.discard(websocket)
                if not conns:
                    self._rooms.pop(room_code.upper(), None)

    def connection_count(self, room_code: str) -> int:
        with self._lock:
            return len(self._rooms.get(room_code.upper(), set()))

    def online_room_codes(self) -> set[str]:
        with self._lock:
            return {code for code, conns in self._rooms.items() if conns}

    async def broadcast_text(self, room_code: str, *, seq: int, raw_text: str, text: str) -> int:
        """推送转写结果，返回成功送达的连接数。"""
        message = {
            "type": "text",
            "room": room_code.upper(),
            "seq": seq,
            "raw_text": raw_text,
            "text": text,
            "created_at": utcnow().isoformat() + "Z",
        }
        with self._lock:
            conns = list(self._rooms.get(room_code.upper(), set()))
        delivered = 0
        failed: list[WebSocket] = []
        for websocket in conns:
            try:
                await websocket.send_json(message)
                delivered += 1
            except Exception:
                failed.append(websocket)
        if failed:
            with self._lock:
                room_conns = self._rooms.get(room_code.upper())
                if room_conns is not None:
                    for websocket in failed:
                        room_conns.discard(websocket)
        return delivered


voice_room_hub = VoiceRoomHub()


def next_voice_seq(db: Session, room_id: int) -> int:
    value = db.scalar(
        select(VoiceMessage.seq).where(VoiceMessage.room_id == room_id).order_by(VoiceMessage.seq.desc()).limit(1)
    )
    return (value or 0) + 1


def record_voice_log(
    db: Session,
    *,
    room_id: int,
    seq: int | None,
    kind: str,
    raw_text: str = "",
    text: str = "",
    detail: dict[str, Any] | None = None,
) -> VoiceLog:
    row = VoiceLog(
        room_id=room_id,
        seq=seq,
        kind=kind,
        raw_text=raw_text,
        text=text,
        detail_json=json.dumps(detail, ensure_ascii=False) if detail else None,
    )
    db.add(row)
    db.flush()
    return row


def room_payload(
    db: Session,
    room: VoiceRoom,
    *,
    base_url: str = "",
    include_messages: bool = False,
    recent_count: int = 5,
) -> dict[str, Any]:
    online = voice_room_hub.connection_count(room.code)
    payload: dict[str, Any] = {
        "id": room.id,
        "code": room.code,
        "name": room.name,
        "status": room.status,
        "created_by": room.created_by,
        "created_at": room.created_at,
        "online_connections": online,
    }
    if base_url:
        payload["mobile_url"] = f"{base_url}/voice/mobile?room={room.code}"
    if include_messages:
        messages = db.scalars(
            select(VoiceMessage)
            .where(VoiceMessage.room_id == room.id)
            .order_by(VoiceMessage.id.desc())
            .limit(recent_count)
        ).all()
        payload["recent_messages"] = [
            {
                "id": message.id,
                "seq": message.seq,
                "raw_text": message.raw_text,
                "text": message.text,
                "stt_model": message.stt_model,
                "llm_model": message.llm_model,
                "delivered_count": message.delivered_count,
                "acked_count": message.acked_count,
                "audio_size": message.audio_size,
                "client_ip": message.client_ip,
                "created_at": message.created_at,
            }
            for message in reversed(messages)
        ]
        logs = db.scalars(
            select(VoiceLog)
            .where(VoiceLog.room_id == room.id)
            .order_by(VoiceLog.id.desc())
            .limit(30)
        ).all()
        payload["recent_logs"] = [
            {
                "id": log.id,
                "seq": log.seq,
                "kind": log.kind,
                "raw_text": log.raw_text,
                "text": log.text,
                "detail": json.loads(log.detail_json) if log.detail_json else None,
                "created_at": log.created_at,
            }
            for log in reversed(logs)
        ]
    return payload

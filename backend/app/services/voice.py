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
from app.crypto import decrypt_secret, encrypt_secret
from app.models import UpstreamAccount, VoiceLog, VoiceMessage, VoiceRoom, VoiceSettings
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
    stt_api_key: str = ""
    stt_ws_url: str = ""
    stt_model: str = "qwen-audio-3.0-asr-flash-streaming"
    llm_account: VoiceAccount = field(default_factory=VoiceAccount)
    llm_model: str = "gpt-4o-mini"
    llm_prompt: str = ""

    @property
    def stt_ready(self) -> bool:
        return bool(self.stt_api_key)

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
    secret_key = env.app_secret_key
    stt_api_key = ""
    if row is not None and row.stt_api_key_encrypted:
        stt_api_key = decrypt_secret(row.stt_api_key_encrypted, secret_key)
    if not stt_api_key:
        stt_api_key = env.aliyun_asr_api_key
    llm_account_id = row.llm_account_id if row is not None else None
    return VoiceConfig(
        stt_api_key=stt_api_key,
        stt_ws_url=env.aliyun_asr_ws_url,
        stt_model=env.aliyun_asr_model,
        llm_account=_account_voice_view(db, llm_account_id),
        llm_model=(row.llm_model if row is not None and row.llm_model else env.voice_llm_model) or "gpt-4o-mini",
        llm_prompt=(row.llm_prompt if row is not None and row.llm_prompt else env.voice_llm_prompt) or DEFAULT_LLM_PROMPT,
    )


def save_voice_config(
    db: Session,
    *,
    stt_api_key: str | None = None,
    llm_account_id: int | None = None,
    llm_model: str = "",
    llm_prompt: str = "",
) -> VoiceSettings:
    row = db.scalar(select(VoiceSettings).order_by(VoiceSettings.id).limit(1))
    if row is None:
        row = VoiceSettings()
        db.add(row)
    if stt_api_key is not None:
        secret_key = get_settings().app_secret_key
        row.stt_api_key_encrypted = (
            encrypt_secret(stt_api_key.strip(), secret_key) if stt_api_key.strip() else None
        )
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
    secret_key = env.app_secret_key
    stt_api_key = ""
    if row is not None and row.stt_api_key_encrypted:
        stt_api_key = decrypt_secret(row.stt_api_key_encrypted, secret_key)
    if not stt_api_key:
        stt_api_key = env.aliyun_asr_api_key
    llm_account_id = row.llm_account_id if row is not None else None
    llm_view = _account_voice_view(db, llm_account_id)
    return {
        "stt_configured": bool(stt_api_key),
        "stt_model": env.aliyun_asr_model,
        "stt_ws_url": env.aliyun_asr_ws_url,
        "llm_account_id": llm_account_id,
        "llm_account_name": llm_view.name,
        "llm_model": (row.llm_model if row is not None and row.llm_model else env.voice_llm_model) or "gpt-4o-mini",
        "llm_configured": llm_view.ready,
        "llm_prompt": (row.llm_prompt if row is not None and row.llm_prompt else env.voice_llm_prompt) or DEFAULT_LLM_PROMPT,
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


def _headers_for(account: VoiceAccount) -> dict[str, str]:
    headers: dict[str, str] = {}
    if account.api_key:
        headers["Authorization"] = f"Bearer {account.api_key}"
    if account.header_spoof and account.header_spoof != "none":
        from app.services.header_spoof import spoof_headers

        headers.update(spoof_headers(account.header_spoof, model=None))
    return headers


async def polish_text(config: VoiceConfig, raw_text: str) -> str:
    """用 LLM 优化转写文本表达。未配置 LLM 时原样返回。"""
    if not config.llm_ready or not raw_text.strip():
        return raw_text
    url = f"{config.llm_account.base_url.rstrip('/')}/chat/completions"
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
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as error:
        raise RuntimeError(f"大模型优化服务连接失败：{error}") from error
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


@dataclass
class VoiceRoomHub:
    """房间级 WebSocket 会话，把识别/优化结果推送给房间内所有连接（桌面端小程序）。"""

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

    async def broadcast(self, room_code: str, message: dict[str, Any]) -> int:
        """推送消息到房间内所有连接，返回成功送达数。"""
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

    async def broadcast_transcript(self, room_code: str, *, seq: int, text: str) -> int:
        """推送单句识别结果（桌面端逐句追加输入）。"""
        return await self.broadcast(
            room_code,
            {
                "type": "transcript",
                "room": room_code.upper(),
                "seq": seq,
                "text": text,
                "created_at": utcnow().isoformat() + "Z",
            },
        )

    async def broadcast_optimized(self, room_code: str, *, seq: int, raw_text: str, text: str) -> int:
        """推送 LLM 优化结果（桌面端回退之前输入的识别文本，替换为优化文本）。"""
        return await self.broadcast(
            room_code,
            {
                "type": "optimized",
                "room": room_code.upper(),
                "seq": seq,
                "raw_text": raw_text,
                "text": text,
                "created_at": utcnow().isoformat() + "Z",
            },
        )


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

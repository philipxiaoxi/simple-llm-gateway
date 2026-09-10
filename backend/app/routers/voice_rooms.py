"""语音房间：管理端 HTTP + 手机/桌面端 WebSocket。

路径约定（重要）：
- 管理接口一律 /api/admin/voice/**（前端 api.ts 只对 /api/admin 前缀做 401 自动登出）
- 公开接口 /api/voice/**，自行校验房间令牌
- WebSocket 不能用 APIRouter(dependencies=[...]) 挂依赖（会把同步 Depends(get_db) 的生命周期
  拉长到整条连接），因此在 handler 内做 in-band 鉴权，失败统一 close(code=1008)。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, get_session_factory
from app.deps import get_current_admin
from app.models import UpstreamAccount, VoiceClient, VoiceEvent, VoiceRoom, VoiceSegment, VoiceSession
from app.services.key_models import is_account_available
from app.services.model_caps import first_model_id
from app.services.voice_hub import ROLE_DESKTOP, ROLE_PHONE, VoiceConnection, voice_hub
from app.services.voice_polish import MODE_ERROR_FIX, MODE_OFF, MODE_REWRITE, VALID_MODES, polish_text
from app.services.voice_session import (
    latest_segments,
    record_voice_event_async,
    recent_segments_for_desktop,
    room_client_counts,
    touch_client,
    voice_session_manager,
)

router = APIRouter(tags=["voice"])
public_router = APIRouter(prefix="/api/voice", tags=["voice-public"])
admin_router = APIRouter(prefix="/api/admin/voice", tags=["admin-voice"], dependencies=[Depends(get_current_admin)])

ROOM_ID_ALPHABET = string.ascii_lowercase + string.digits
MAX_AUDIO_FRAME_BYTES = 1 << 20  # 单帧上限 1MB，防止恶意大包
ALLOWED_ASR_MODELS = (
    "qwen-audio-3.0-asr-flash-streaming",
    "fun-asr-realtime",
    "paraformer-realtime-v2",
    "paraformer-realtime-v1",
)


# ---------------------------------------------------------------- 工具


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _new_room_id() -> str:
    return "".join(secrets.choice(ROOM_ID_ALPHABET) for _ in range(8))


def _new_join_code() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))


def _valid_room_id(value: str) -> bool:
    return bool(value) and len(value) == 8 and all(ch in ROOM_ID_ALPHABET for ch in value)


def get_room_by_room_id(db: Session, room_id: str) -> VoiceRoom | None:
    return db.scalar(select(VoiceRoom).where(VoiceRoom.room_id == room_id))


def require_room(db: Session, room_id: str) -> VoiceRoom:
    room = get_room_by_room_id(db, room_id)
    if room is None or room.status != "active":
        raise HTTPException(status_code=404, detail="语音房不存在或已停用")
    return room


def mint_room_token(*, room_id: str, role: str, client_uid: str, ttl_seconds: int, admin_ver: int | None = None) -> str:
    """签发房间作用域令牌。手机/桌面客户端无法设置 Authorization 头，所以用 query/首帧传递。"""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "scope": "voice",
        "role": role,
        "room_id": room_id,
        "cid": client_uid,
        "iat": now,
        "exp": now + timedelta(seconds=max(60, ttl_seconds)),
    }
    if admin_ver is not None:
        payload["ver"] = admin_ver
    return jwt.encode(payload, get_settings().app_secret_key, algorithm="HS256")


class TokenError(Exception):
    pass


def verify_room_token(token: str | None, *, room_id: str, role: str) -> dict[str, Any]:
    if not token:
        raise TokenError("缺少令牌")
    try:
        payload = jwt.decode(token, get_settings().app_secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise TokenError("令牌无效或已过期") from exc
    if payload.get("scope") != "voice":
        raise TokenError("令牌作用域不匹配")
    if payload.get("role") != role:
        raise TokenError("令牌角色不匹配")
    if payload.get("room_id") != room_id:
        raise TokenError("令牌与房间不匹配")
    return payload


def _room_payload(room: VoiceRoom, *, include_secret: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": room.id,
        "roomId": room.room_id,
        "name": room.name,
        "status": room.status,
        "joinCode": room.join_code,
        "requirePin": bool(room.pin_hash),
        "note": room.note,
        "asrProvider": room.asr_provider,
        "asrModel": room.asr_model,
        "disfluencyRemoval": bool(room.disfluency_removal),
        "maxRecordingSeconds": room.max_recording_seconds,
        "polishMode": room.polish_mode,
        "polishAccountId": room.polish_account_id,
        "polishModel": room.polish_model,
        "polishSystemPrompt": room.polish_system_prompt,
        "polishTemperature": room.polish_temperature,
        "logPartials": bool(room.log_partials),
        "createdAt": room.created_at.isoformat() if room.created_at else None,
        "updatedAt": room.updated_at.isoformat() if room.updated_at else None,
    }
    if include_secret:
        data["phoneTokenTtlSeconds"] = room.phone_token_ttl_seconds
    return data


def _segment_payload(row: VoiceSegment, account_name: str | None = None) -> dict[str, Any]:
    end_to_end_ms = None
    if row.finalized_at and row.created_at:
        end_to_end_ms = int((row.finalized_at - row.created_at).total_seconds() * 1000)
    return {
        "id": row.id,
        "segId": row.seg_uid,
        "sessionId": row.session_id,
        "seq": row.seq,
        "rev": row.rev,
        "state": row.state,
        "rawText": row.raw_text,
        "polishedText": row.polished_text,
        "polishStatus": row.polish_status,
        "polishModel": row.polish_model,
        "polishAccountId": row.polish_account_id,
        "polishAccountName": account_name,
        "polishMs": row.polish_ms,
        "polishError": row.polish_error,
        "asrBeginMs": row.asr_begin_ms,
        "asrEndMs": row.asr_end_ms,
        "deliverCount": row.deliver_count,
        "ackCount": row.ack_count,
        "endToEndMs": end_to_end_ms,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _event_payload(row: VoiceEvent) -> dict[str, Any]:
    payload = None
    if row.payload_json:
        with contextlib.suppress(Exception):
            payload = json.loads(row.payload_json)
    return {
        "id": row.id,
        "kind": row.kind,
        "level": row.level,
        "message": row.message,
        "payload": payload,
        "sessionId": row.session_id,
        "segmentId": row.segment_id,
        "clientId": row.client_id,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _pick_default_account(db: Session) -> UpstreamAccount | None:
    rows = db.scalars(select(UpstreamAccount).order_by(UpstreamAccount.id)).all()
    for account in rows:
        if is_account_available(account):
            return account
    return None


# ---------------------------------------------------------------- 管理端 HTTP


class VoiceRoomCreate(BaseModel):
    name: str = Field(default="我的语音房", max_length=128)
    pin: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=255)
    asrModel: str | None = None
    disfluencyRemoval: bool = True
    maxRecordingSeconds: int = Field(default=120, ge=10, le=600)
    polishMode: str = MODE_ERROR_FIX
    polishAccountId: int | None = None
    polishModel: str | None = Field(default=None, max_length=128)
    polishSystemPrompt: str | None = None
    polishTemperature: float = Field(default=0.2, ge=0.0, le=1.5)
    logPartials: bool = False


class VoiceRoomUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    status: str | None = None
    pin: str | None = Field(default=None, max_length=32)
    clearPin: bool = False
    note: str | None = Field(default=None, max_length=255)
    asrModel: str | None = None
    disfluencyRemoval: bool | None = None
    maxRecordingSeconds: int | None = Field(default=None, ge=10, le=600)
    polishMode: str | None = None
    polishAccountId: int | None = None
    polishModel: str | None = Field(default=None, max_length=128)
    polishSystemPrompt: str | None = None
    polishTemperature: float | None = Field(default=None, ge=0.0, le=1.5)
    logPartials: bool | None = None


class TestPolishRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    accountId: int | None = None
    model: str | None = None
    mode: str = MODE_ERROR_FIX
    systemPrompt: str | None = None
    roomId: str | None = None


@admin_router.get("/rooms")
def list_rooms(db: Session = Depends(get_db)) -> dict[str, Any]:
    rooms = db.scalars(select(VoiceRoom).order_by(VoiceRoom.id.desc())).all()
    items = []
    for room in rooms:
        state = voice_hub.room_state(room.room_id)
        counts = room_client_counts(room.id)
        today = _now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_segments = db.scalar(
            select(func.count(VoiceSegment.id)).where(
                VoiceSegment.room_pk == room.id, VoiceSegment.created_at >= today
            )
        )
        items.append(
            {
                **_room_payload(room),
                "online": state,
                "counts": counts,
                "todaySegments": int(today_segments or 0),
            }
        )
    return {"items": items, "total": len(items)}


@admin_router.post("/rooms")
def create_room(payload: VoiceRoomCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    model = (payload.asrModel or "").strip() or get_settings().aliyun_asr_model
    if model not in ALLOWED_ASR_MODELS:
        raise HTTPException(status_code=400, detail=f"不支持的语音识别模型：{model}")
    if payload.polishMode not in VALID_MODES:
        raise HTTPException(status_code=400, detail="纠错档位只能是 off / error_fix / rewrite")

    account_id = payload.polishAccountId
    if payload.polishMode != MODE_OFF and account_id is None:
        account = _pick_default_account(db)
        account_id = account.id if account else None

    room_id = _new_room_id()
    while get_room_by_room_id(db, room_id) is not None:
        room_id = _new_room_id()
    join_code = _new_join_code()
    while db.scalar(select(VoiceRoom).where(VoiceRoom.join_code == join_code)) is not None:
        join_code = _new_join_code()

    room = VoiceRoom(
        room_id=room_id,
        name=payload.name.strip() or "我的语音房",
        join_code=join_code,
        pin_hash=_hash_pin(payload.pin),
        note=payload.note,
        asr_model=model,
        disfluency_removal=payload.disfluencyRemoval,
        max_recording_seconds=payload.maxRecordingSeconds,
        polish_mode=payload.polishMode,
        polish_account_id=account_id,
        polish_model=(payload.polishModel or "").strip() or None,
        polish_system_prompt=payload.polishSystemPrompt,
        polish_temperature=payload.polishTemperature,
        log_partials=payload.logPartials,
    )
    db.add(room)
    db.commit()
    db.refresh(room)
    return _room_payload(room, include_secret=True)


@admin_router.get("/rooms/{room_id}")
def get_room(room_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    room = require_room(db, room_id)
    return _room_payload(room, include_secret=True)


@admin_router.patch("/rooms/{room_id}")
def update_room(room_id: str, payload: VoiceRoomUpdate, db: Session = Depends(get_db)) -> dict[str, Any]:
    room = require_room(db, room_id)
    if payload.name is not None:
        room.name = payload.name.strip() or room.name
    if payload.status is not None:
        if payload.status not in {"active", "disabled"}:
            raise HTTPException(status_code=400, detail="状态只能是 active / disabled")
        room.status = payload.status
    if payload.clearPin:
        room.pin_hash = None
    elif payload.pin is not None:
        room.pin_hash = _hash_pin(payload.pin)
    if payload.note is not None:
        room.note = payload.note
    if payload.asrModel is not None:
        model = payload.asrModel.strip()
        if model not in ALLOWED_ASR_MODELS:
            raise HTTPException(status_code=400, detail=f"不支持的语音识别模型：{model}")
        room.asr_model = model
    if payload.disfluencyRemoval is not None:
        room.disfluency_removal = payload.disfluencyRemoval
    if payload.maxRecordingSeconds is not None:
        room.max_recording_seconds = payload.maxRecordingSeconds
    if payload.polishMode is not None:
        if payload.polishMode not in VALID_MODES:
            raise HTTPException(status_code=400, detail="纠错档位只能是 off / error_fix / rewrite")
        room.polish_mode = payload.polishMode
    if payload.polishAccountId is not None:
        room.polish_account_id = payload.polishAccountId or None
    if payload.polishModel is not None:
        room.polish_model = payload.polishModel.strip() or None
    if payload.polishSystemPrompt is not None:
        room.polish_system_prompt = payload.polishSystemPrompt or None
    if payload.polishTemperature is not None:
        room.polish_temperature = payload.polishTemperature
    if payload.logPartials is not None:
        room.log_partials = payload.logPartials
    room.updated_at = _now()
    db.commit()
    db.refresh(room)
    return _room_payload(room, include_secret=True)


@admin_router.delete("/rooms/{room_id}")
def delete_room(room_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    room = require_room(db, room_id)
    voice_hub.set_busy(room.room_id, False)
    voice_session_manager.drop_room(room.room_id)
    db.query(VoiceEvent).filter(VoiceEvent.room_pk == room.id).delete(synchronize_session=False)
    db.query(VoiceSegment).filter(VoiceSegment.room_pk == room.id).delete(synchronize_session=False)
    db.query(VoiceSession).filter(VoiceSession.room_pk == room.id).delete(synchronize_session=False)
    db.query(VoiceClient).filter(VoiceClient.room_pk == room.id).delete(synchronize_session=False)
    db.delete(room)
    db.commit()
    return {"ok": True}


@admin_router.post("/rooms/{room_id}/rotate-code")
def rotate_code(room_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    room = require_room(db, room_id)
    code = _new_join_code()
    while db.scalar(select(VoiceRoom).where(VoiceRoom.join_code == code)) is not None:
        code = _new_join_code()
    room.join_code = code
    room.updated_at = _now()
    db.commit()
    db.refresh(room)
    return _room_payload(room, include_secret=True)


@admin_router.post("/rooms/{room_id}/token")
def issue_token(room_id: str, role: str = Query(default="desktop"), days: int = Query(default=30, ge=1, le=365), db: Session = Depends(get_db)) -> dict[str, Any]:
    """给桌面客户端签发房间令牌（手机令牌由 /join 自动签发）。"""
    room = require_room(db, room_id)
    if role not in {ROLE_PHONE, ROLE_DESKTOP}:
        raise HTTPException(status_code=400, detail="角色只能是 phone / desktop")
    token = mint_room_token(
        room_id=room.room_id,
        role=role,
        client_uid=f"manual-{role}",
        ttl_seconds=days * 86400,
    )
    return {
        "token": token,
        "roomId": room.room_id,
        "wsUrl": f"/api/voice/desktop/connect",
        "expiresInSeconds": days * 86400,
        "desktopConfig": {
            "serverUrl": _absolute_ws_url(f"/api/voice/desktop/connect"),
            "roomId": room.room_id,
            "token": token,
        },
    }


@admin_router.get("/rooms/{room_id}/live")
def room_live(room_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    room = require_room(db, room_id)
    active = db.scalars(
        select(VoiceSession)
        .where(VoiceSession.room_pk == room.id)
        .order_by(VoiceSession.id.desc())
        .limit(10)
    ).all()
    return {
        "room": _room_payload(room, include_secret=True),
        "state": voice_hub.room_state(room.room_id),
        "onlineClients": voice_hub.online_clients(room.room_id),
        "counts": room_client_counts(room.id),
        "recentSegments": latest_segments(room.id, 30),
        "asrConfigured": bool(get_settings().aliyun_dashscope_api_key.strip()),
        "sessions": [
            {
                "id": row.id,
                "sessionUid": row.session_uid,
                "status": row.status,
                "clientName": row.client_name,
                "audioMs": row.audio_ms,
                "sentenceCount": row.sentence_count,
                "asrUsageSeconds": row.asr_usage_seconds,
                "startedAt": row.started_at.isoformat() if row.started_at else None,
                "endedAt": row.ended_at.isoformat() if row.ended_at else None,
            }
            for row in active
        ],
    }


@admin_router.get("/rooms/{room_id}/sessions")
def room_sessions(
    room_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    room = require_room(db, room_id)
    total = int(
        db.scalar(select(func.count(VoiceSession.id)).where(VoiceSession.room_pk == room.id)) or 0
    )
    rows = db.scalars(
        select(VoiceSession)
        .where(VoiceSession.room_pk == room.id)
        .order_by(VoiceSession.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "sessionUid": row.session_uid,
                "clientName": row.client_name,
                "status": row.status,
                "asrModel": row.asr_model,
                "audioMs": row.audio_ms,
                "frameCount": row.frame_count,
                "sentenceCount": row.sentence_count,
                "asrUsageSeconds": row.asr_usage_seconds,
                "firstPartialMs": row.first_partial_ms,
                "errorMessage": row.error_message,
                "startedAt": row.started_at.isoformat() if row.started_at else None,
                "endedAt": row.ended_at.isoformat() if row.ended_at else None,
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@admin_router.get("/rooms/{room_id}/segments")
def room_segments(
    room_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    session_id: int | None = None,
    keyword: str | None = None,
    polish_status: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    room = require_room(db, room_id)
    conditions = [VoiceSegment.room_pk == room.id]
    if session_id:
        conditions.append(VoiceSegment.session_id == session_id)
    if keyword:
        like = f"%{keyword}%"
        conditions.append(VoiceSegment.raw_text.like(like) | VoiceSegment.polished_text.like(like))
    if polish_status == "none":
        conditions.append(VoiceSegment.polished_text.is_(None))
    elif polish_status:
        conditions.append(VoiceSegment.polish_status == polish_status)

    total = int(db.scalar(select(func.count(VoiceSegment.id)).where(*conditions)) or 0)
    rows = db.scalars(
        select(VoiceSegment)
        .where(*conditions)
        .order_by(VoiceSegment.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    account_ids = {row.polish_account_id for row in rows if row.polish_account_id}
    names: dict[int, str] = {}
    if account_ids:
        for account in db.scalars(select(UpstreamAccount).where(UpstreamAccount.id.in_(account_ids))):
            names[account.id] = account.name
    return {
        "items": [_segment_payload(row, names.get(row.polish_account_id or -1)) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@admin_router.get("/rooms/{room_id}/events")
def room_events(
    room_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    kind: str | None = None,
    level: str | None = None,
    session_id: int | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    room = require_room(db, room_id)
    conditions = [VoiceEvent.room_pk == room.id]
    if kind:
        conditions.append(VoiceEvent.kind == kind)
    if level:
        conditions.append(VoiceEvent.level == level)
    if session_id:
        conditions.append(VoiceEvent.session_id == session_id)
    total = int(db.scalar(select(func.count(VoiceEvent.id)).where(*conditions)) or 0)
    rows = db.scalars(
        select(VoiceEvent)
        .where(*conditions)
        .order_by(VoiceEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    kinds = [
        row[0]
        for row in db.execute(
            select(VoiceEvent.kind).where(VoiceEvent.room_pk == room.id).group_by(VoiceEvent.kind)
        )
    ]
    return {
        "items": [_event_payload(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "kinds": sorted(kinds),
    }


@admin_router.get("/polish-accounts")
def polish_accounts(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(UpstreamAccount).order_by(UpstreamAccount.id)).all()
    items = []
    for account in rows:
        models: list[str] = []
        raw = account.models_json
        if raw:
            with contextlib.suppress(Exception):
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    for entry in parsed:
                        if isinstance(entry, str):
                            models.append(entry)
                        elif isinstance(entry, dict) and entry.get("id"):
                            models.append(str(entry["id"]))
        items.append(
            {
                "id": account.id,
                "name": account.name,
                "provider": account.provider,
                "source": account.source,
                "available": is_account_available(account),
                "defaultModel": first_model_id(account.models_json),
                "models": models[:200],
            }
        )
    return {"items": items, "total": len(items)}


@admin_router.post("/test-polish")
async def test_polish(payload: TestPolishRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """用一段文本试跑纠错。同时返回原文与模型输出，便于判断这一跳有没有价值。"""
    mode = payload.mode if payload.mode in VALID_MODES else MODE_ERROR_FIX
    account_id = payload.accountId
    model = payload.model
    system_prompt = payload.systemPrompt
    temperature = 0.2
    if payload.roomId:
        room = get_room_by_room_id(db, payload.roomId)
        if room is not None:
            account_id = account_id or room.polish_account_id
            model = model or room.polish_model
            system_prompt = system_prompt or room.polish_system_prompt
            temperature = room.polish_temperature
    result = await polish_text(
        db,
        account_id=account_id,
        model=model,
        text=payload.text,
        system_prompt=system_prompt,
        mode=mode,
        temperature=temperature,
    )
    return {
        "status": result.status,
        "input": result.raw,
        "output": result.text,
        "changed": result.text != result.raw,
        "model": result.model,
        "accountId": result.account_id,
        "ms": result.ms,
        "error": result.error,
        "reason": result.reason,
    }


# ---------------------------------------------------------------- 公开 HTTP（手机端）


class JoinRequest(BaseModel):
    code: str | None = Field(default=None, max_length=16)
    pin: str | None = Field(default=None, max_length=32)
    clientUid: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=64)


@public_router.get("/rooms/lookup")
def lookup_room(code: str = Query(min_length=4, max_length=16), db: Session = Depends(get_db)) -> dict[str, Any]:
    room = db.scalar(select(VoiceRoom).where(VoiceRoom.join_code == code.strip()))
    if room is None or room.status != "active":
        raise HTTPException(status_code=404, detail="房间不存在或已停用")
    return {"roomId": room.room_id, "name": room.name, "requirePin": bool(room.pin_hash)}


@public_router.post("/rooms/{room_id}/join")
def join_room(room_id: str, payload: JoinRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    if not _valid_room_id(room_id):
        raise HTTPException(status_code=404, detail="房间不存在或已停用")
    room = require_room(db, room_id)
    if payload.code and payload.code.strip() != room.join_code:
        raise HTTPException(status_code=403, detail="房间码不正确")
    if room.pin_hash:
        if not payload.pin or not _verify_pin(payload.pin, room.pin_hash):
            raise HTTPException(status_code=403, detail="房间口令不正确")
    token = mint_room_token(
        room_id=room.room_id,
        role=ROLE_PHONE,
        client_uid=payload.clientUid,
        ttl_seconds=room.phone_token_ttl_seconds,
    )
    return {
        "token": token,
        "expiresInSeconds": room.phone_token_ttl_seconds,
        "wsPath": f"/api/voice/rooms/{room.room_id}/phone",
        "wsUrl": _absolute_ws_url(f"/api/voice/rooms/{room.room_id}/phone"),
        "room": {
            "roomId": room.room_id,
            "name": room.name,
            "asrModel": room.asr_model,
            "maxRecordingSeconds": room.max_recording_seconds,
            "polishMode": room.polish_mode,
        },
    }


# ---------------------------------------------------------------- WebSocket：手机端


@router.websocket("/api/voice/rooms/{room_id}/phone")
async def phone_socket(websocket: WebSocket, room_id: str) -> None:
    await websocket.accept()
    connection: VoiceConnection | None = None
    session: Any = None
    client_id: int | None = None
    room_pk: int | None = None
    try:
        if not _valid_room_id(room_id):
            await websocket.close(code=1008, reason="房间不存在")
            return
        try:
            claims = verify_room_token(websocket.query_params.get("token"), room_id=room_id, role=ROLE_PHONE)
        except TokenError as exc:
            await websocket.close(code=1008, reason=str(exc))
            return

        room = await asyncio.to_thread(_load_room_for_ws, room_id)
        if room is None:
            await websocket.close(code=1008, reason="房间不存在或已停用")
            return
        room_pk = room.id
        client_uid = str(claims.get("cid") or "phone")

        # 首帧必须是 hello
        first = await websocket.receive()
        if first.get("type") == "websocket.disconnect":
            return
        hello = _parse_json(first.get("text"))
        if hello is None or hello.get("type") != "hello":
            await websocket.close(code=1008, reason="首帧必须是 hello")
            return
        client_uid = str(hello.get("clientUid") or client_uid)
        name = str(hello.get("name") or "手机")[:64]

        connection = VoiceConnection(
            room_id=room.room_id, role=ROLE_PHONE, client_uid=client_uid, websocket=websocket, name=name
        )
        voice_hub.register(connection)
        row = await asyncio.to_thread(
            touch_client, room_pk=room_pk, client_uid=client_uid, role=ROLE_PHONE, name=name, online=True
        )
        client_id = row.id if row else None
        await record_voice_event_async(
            room_pk,
            "room.join",
            client_id=client_id,
            message=f"{name} 加入房间",
            payload={"role": ROLE_PHONE, "name": name, "clientUid": client_uid},
        )
        await connection.send_json(
            {
                "type": "ready",
                "sessionUid": None,
                "asrModel": room.asr_model,
                "maxRecordingSeconds": room.max_recording_seconds,
                "polishMode": room.polish_mode,
                "room": {"roomId": room.room_id, "name": room.name},
                "roomState": voice_hub.room_state(room.room_id),
                "ts": _now_ms(),
            }
        )
        await voice_hub.broadcast_room_state(room.room_id)

        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            raw_bytes = message.get("bytes")
            if raw_bytes is not None:
                if session is None or len(raw_bytes) > MAX_AUDIO_FRAME_BYTES:
                    continue
                await voice_session_manager.push_audio(session, raw_bytes)
                continue
            frame = _parse_json(message.get("text"))
            if frame is None:
                continue
            kind = frame.get("type")
            if kind == "ping":
                await connection.send_json({"type": "pong", "ts": _now_ms()})
                continue
            if kind == "start":
                if session is not None:
                    await voice_session_manager.stop(room=room, session=session, discarded=True)
                    session = None
                try:
                    session = await voice_session_manager.start(
                        room=room,
                        connection=connection,
                        client_id=client_id,
                        recording_id=frame.get("recordingId"),
                    )
                    await connection.send_json(
                        {
                            "type": "session.started",
                            "sessionUid": session.session_uid,
                            "asrModel": session.asr_model,
                            "ts": _now_ms(),
                        }
                    )
                except Exception as exc:  # AsrError 及未知异常都转成客户端可读的错误
                    code = getattr(exc, "code", "ASR_FAILED")
                    session = None
                    await connection.send_json(
                        {"type": "error", "code": code, "message": str(exc), "ts": _now_ms()}
                    )
                continue
            if kind == "stop":
                if session is not None:
                    await voice_session_manager.stop(room=room, session=session, discarded=False)
                    session = None
                continue
            if kind == "discard":
                if session is not None:
                    await voice_session_manager.stop(room=room, session=session, discarded=True)
                    session = None
                continue
    except WebSocketDisconnect:
        pass
    except Exception:
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="服务端内部错误")
    finally:
        if session is not None and room_pk is not None:
            with contextlib.suppress(Exception):
                room_obj = await asyncio.to_thread(_load_room_for_ws, room_id)
                if room_obj is not None:
                    await voice_session_manager.stop(room=room_obj, session=session, discarded=True)
        if connection is not None:
            voice_hub.unregister(connection)
            if room_pk is not None:
                await asyncio.to_thread(
                    touch_client,
                    room_pk=room_pk,
                    client_uid=connection.client_uid,
                    role=ROLE_PHONE,
                    name=connection.name,
                    online=False,
                )
                await record_voice_event_async(
                    room_pk,
                    "room.leave",
                    client_id=client_id,
                    message=f"{connection.name or connection.client_uid} 离开房间",
                    payload={"role": ROLE_PHONE},
                )
                await voice_hub.broadcast_room_state(room_id)


# ---------------------------------------------------------------- WebSocket：桌面端


@router.websocket("/api/voice/desktop/connect")
async def desktop_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    connection: VoiceConnection | None = None
    room_pk: int | None = None
    client_id: int | None = None
    try:
        first = await websocket.receive()
        if first.get("type") == "websocket.disconnect":
            return
        frame = _parse_json(first.get("text"))
        if frame is None or frame.get("type") != "register":
            await websocket.close(code=1008, reason="首帧必须是 register")
            return
        room_id = str(frame.get("roomId") or "")
        token = frame.get("token")
        if not _valid_room_id(room_id):
            await websocket.close(code=1008, reason="房间不存在")
            return

        settings = get_settings()
        shared = settings.voice_desktop_token.strip()
        claims: dict[str, Any] | None = None
        if token and shared and secrets.compare_digest(str(token), shared):
            claims = {"cid": frame.get("clientUid") or "desktop"}
        else:
            try:
                claims = verify_room_token(token, room_id=room_id, role=ROLE_DESKTOP)
            except TokenError as exc:
                await websocket.close(code=1008, reason=str(exc))
                return

        room = await asyncio.to_thread(_load_room_for_ws, room_id)
        if room is None:
            await websocket.close(code=1008, reason="房间不存在或已停用")
            return
        room_pk = room.id
        client_uid = str(frame.get("clientUid") or claims.get("cid") or "desktop")
        name = str(frame.get("name") or "电脑")[:64]
        insert_mode = bool(frame.get("insertMode"))

        connection = VoiceConnection(
            room_id=room.room_id, role=ROLE_DESKTOP, client_uid=client_uid, websocket=websocket, name=name
        )
        voice_hub.register(connection)
        row = await asyncio.to_thread(
            touch_client,
            room_pk=room_pk,
            client_uid=client_uid,
            role=ROLE_DESKTOP,
            name=name,
            online=True,
            insert_mode=insert_mode,
        )
        client_id = row.id if row else None
        await record_voice_event_async(
            room_pk,
            "room.join",
            client_id=client_id,
            message=f"{name} 连接",
            payload={"role": ROLE_DESKTOP, "name": name, "clientUid": client_uid, "insertMode": insert_mode},
        )
        await connection.send_json(
            {
                "type": "registered",
                "room": {"roomId": room.room_id, "name": room.name},
                "roomState": voice_hub.room_state(room.room_id),
                "recentSegments": await asyncio.to_thread(recent_segments_for_desktop, room_pk),
                "ts": _now_ms(),
            }
        )
        await voice_hub.broadcast_room_state(room.room_id)

        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = _parse_json(message.get("text"))
            if data is None:
                continue
            kind = data.get("type")
            if kind == "ping":
                await connection.send_json({"type": "pong", "ts": _now_ms()})
                continue
            if kind == "segment.ack":
                await voice_session_manager.record_ack(
                    room=room,
                    client_id=client_id,
                    seg_uid=str(data.get("segId") or ""),
                    rev=int(data.get("rev") or 0),
                    ok=bool(data.get("ok", True)),
                    action=data.get("action"),
                    chars=data.get("chars"),
                    text=data.get("text"),
                )
                continue
            if kind in {"segment.abandoned", "inject.error"}:
                event_kind = "segment.abandoned" if kind == "segment.abandoned" else "desktop.inject_failed"
                await voice_session_manager.record_client_event(
                    room=room,
                    client_id=client_id,
                    kind=event_kind,
                    seg_uid=data.get("segId"),
                    message=str(data.get("reason") or ""),
                    payload={k: v for k, v in data.items() if k != "type"},
                )
                continue
            if kind == "status":
                new_mode = bool(data.get("insertMode"))
                insert_mode = new_mode
                if client_id is not None:
                    await asyncio.to_thread(_update_client_insert_mode, client_id, new_mode)
                await voice_hub.broadcast_room_state(room.room_id)
                continue
    except WebSocketDisconnect:
        pass
    except Exception:
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="服务端内部错误")
    finally:
        if connection is not None:
            voice_hub.unregister(connection)
            if room_pk is not None:
                await asyncio.to_thread(
                    touch_client,
                    room_pk=room_pk,
                    client_uid=connection.client_uid,
                    role=ROLE_DESKTOP,
                    name=connection.name,
                    online=False,
                )
                await record_voice_event_async(
                    room_pk,
                    "room.leave",
                    client_id=client_id,
                    message=f"{connection.name or connection.client_uid} 断开",
                    payload={"role": ROLE_DESKTOP},
                )
                await voice_hub.broadcast_room_state(connection.room_id)


# ---------------------------------------------------------------- 内部工具


def _parse_json(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _load_room_for_ws(room_id: str) -> VoiceRoom | None:
    db = get_session_factory()()
    try:
        room = db.scalar(select(VoiceRoom).where(VoiceRoom.room_id == room_id))
        if room is None or room.status != "active":
            return None
        db.expunge(room)
        return room
    finally:
        db.close()


def _update_client_insert_mode(client_id: int, insert_mode: bool) -> None:
    db = get_session_factory()()
    try:
        row = db.get(VoiceClient, client_id)
        if row is None:
            return
        row.insert_mode = insert_mode
        row.updated_at = _now()
        db.commit()
    finally:
        db.close()


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _absolute_ws_url(path: str) -> str:
    base = (get_settings().voice_public_base_url or get_settings().app_base_url or "").rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return f"{base}{path}"


def _hash_pin(pin: str | None) -> str | None:
    value = (pin or "").strip()
    if not value:
        return None
    from app.crypto import hash_password

    return hash_password(value)


def _verify_pin(pin: str, pin_hash: str) -> bool:
    from app.crypto import verify_password

    try:
        return verify_password(pin, pin_hash)
    except Exception:
        return False

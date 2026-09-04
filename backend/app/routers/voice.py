from __future__ import annotations

import json

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.db import get_db
from app.models import VoiceMessage, VoiceRoom
from app.schemas import VoiceRoomJoinOut, VoiceTranscribeOut
from app.services.voice import (
    next_voice_seq,
    record_voice_log,
    transcribe_audio,
    voice_room_hub,
)

router = APIRouter(prefix="/api/voice", tags=["voice"])


def _resolve_active_room(db: Session, code: str) -> VoiceRoom:
    room = db.scalar(select(VoiceRoom).where(VoiceRoom.code == code.strip().upper()))
    if room is None:
        raise HTTPException(status_code=404, detail="房间不存在")
    if room.status != "active":
        raise HTTPException(status_code=403, detail="房间已关闭")
    return room


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.get("/rooms/{code}", response_model=VoiceRoomJoinOut)
def join_room(code: str, db: Session = Depends(get_db)) -> VoiceRoomJoinOut:
    room = _resolve_active_room(db, code)
    return VoiceRoomJoinOut(code=room.code, name=room.name, status=room.status)


@router.post("/transcribe", response_model=VoiceTranscribeOut)
async def transcribe(
    request: Request,
    room: str = Form(...),
    audio: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> VoiceTranscribeOut:
    resolved_room = _resolve_active_room(db, room)
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="音频为空，请重新录音")
    try:
        result = await transcribe_audio(db, resolved_room, data, filename=audio.filename or "audio.webm")
    except RuntimeError as error:
        record_voice_log(
            db,
            room_id=resolved_room.id,
            seq=None,
            kind="error",
            detail={"message": str(error), "source_ip": _client_ip(request)},
        )
        db.commit()
        raise HTTPException(status_code=502, detail=str(error)) from error

    seq = next_voice_seq(db, resolved_room.id)
    client_ip = _client_ip(request)
    message = VoiceMessage(
        room_id=resolved_room.id,
        seq=seq,
        raw_text=result["raw_text"],
        text=result["text"],
        audio_size=len(data),
        client_ip=client_ip,
        stt_model=result.get("stt_model", ""),
        llm_model=result.get("llm_model", ""),
        stt_latency_ms=result.get("stt_latency_ms"),
        llm_latency_ms=result.get("llm_latency_ms"),
    )
    db.add(message)
    record_voice_log(
        db,
        room_id=resolved_room.id,
        seq=seq,
        kind="transcribed",
        raw_text=result["raw_text"],
        text=result["text"],
        detail={
            "audio_size": len(data),
            "source_ip": client_ip,
            "stt_model": result.get("stt_model", ""),
            "stt_latency_ms": result.get("stt_latency_ms"),
            "llm_model": result.get("llm_model", ""),
            "llm_latency_ms": result.get("llm_latency_ms"),
        },
    )
    db.commit()
    db.refresh(message)
    delivered = await voice_room_hub.broadcast_text(
        resolved_room.code,
        seq=seq,
        raw_text=result["raw_text"],
        text=result["text"],
    )
    if delivered > 0:
        message.delivered_count = delivered
        record_voice_log(
            db,
            room_id=resolved_room.id,
            seq=seq,
            kind="sent",
            raw_text=result["raw_text"],
            text=result["text"],
            detail={"delivered": delivered},
        )
        db.commit()
    return VoiceTranscribeOut(
        seq=seq,
        raw_text=result["raw_text"],
        text=result["text"],
        delivered=delivered,
        stt_model=result.get("stt_model", ""),
        llm_model=result.get("llm_model", ""),
    )


@router.websocket("/ws/{room_code}")
async def voice_ws(room_code: str, websocket: WebSocket, db: Session = Depends(get_db)) -> None:
    room = db.scalar(select(VoiceRoom).where(VoiceRoom.code == room_code.strip().upper()))
    if room is None or room.status != "active":
        await websocket.close(code=4404, reason="房间不存在或已关闭")
        return
    await voice_room_hub.connect(room.code, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if frame.get("type") == "ack":
                seq = int(frame.get("seq") or 0)
                message = db.scalar(
                    select(VoiceMessage).where(
                        VoiceMessage.room_id == room.id, VoiceMessage.seq == seq
                    )
                )
                if message is not None:
                    message.acked_count += 1
                    message.ack_at = utcnow()
                    record_voice_log(
                        db,
                        room_id=room.id,
                        seq=seq,
                        kind="acked",
                        raw_text=message.raw_text,
                        text=message.text,
                        detail={
                            "acked_count": message.acked_count,
                            "method": frame.get("method"),
                            "ok": frame.get("ok", True),
                            "warn": frame.get("warn"),
                        },
                    )
                    db.commit()
            elif frame.get("type") == "hello":
                await websocket.send_json({"type": "connected", "room": room.code})
    except WebSocketDisconnect:
        pass
    finally:
        await voice_room_hub.disconnect(room.code, websocket)

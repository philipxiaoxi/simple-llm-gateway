from __future__ import annotations

import asyncio
import json

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.db import get_db
from app.models import VoiceMessage, VoiceRoom
from app.schemas import VoiceRoomJoinOut
from app.services.aliyun_asr import AliyunAsrError, AliyunAsrSession
from app.services.voice import (
    load_voice_config,
    next_voice_seq,
    polish_text,
    record_voice_log,
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


@router.get("/rooms/{code}", response_model=VoiceRoomJoinOut)
def join_room(code: str, db: Session = Depends(get_db)) -> VoiceRoomJoinOut:
    room = _resolve_active_room(db, code)
    return VoiceRoomJoinOut(code=room.code, name=room.name, status=room.status)


@router.websocket("/asr/{room_code}")
async def voice_asr(room_code: str, websocket: WebSocket, db: Session = Depends(get_db)) -> None:
    """手机端实时语音识别：上行 PCM 音频流，下行实时转写文本。"""
    room = db.scalar(select(VoiceRoom).where(VoiceRoom.code == room_code.strip().upper()))
    if room is None or room.status != "active":
        await websocket.close(code=4404, reason="房间不存在或已关闭")
        return

    await websocket.accept()
    config = load_voice_config(db)
    if not config.stt_ready:
        await websocket.send_json({"type": "error", "message": "阿里云语音识别未配置，请到管理后台「语音输入」→「语音配置」填写 API Key"})
        await websocket.close(code=4000)
        return

    seq = next_voice_seq(db, room.id)
    client_ip = websocket.client.host if websocket.client else None
    sentences: list[str] = []
    audio_bytes_total = 0

    session = AliyunAsrSession(
        config.stt_api_key,
        ws_url=config.stt_ws_url,
        model=config.stt_model,
    )
    try:
        await session.start()
    except AliyunAsrError as error:
        record_voice_log(db, room_id=room.id, seq=seq, kind="error", detail={"message": str(error), "source_ip": client_ip})
        db.commit()
        await websocket.send_json({"type": "error", "message": str(error)})
        await websocket.close(code=4000)
        return
    except Exception as error:
        record_voice_log(db, room_id=room.id, seq=seq, kind="error", detail={"message": str(error), "source_ip": client_ip})
        db.commit()
        await websocket.send_json({"type": "error", "message": f"无法连接阿里云语音识别：{error}"})
        await websocket.close(code=4000)
        return

    async def pump_phone() -> None:
        nonlocal audio_bytes_total
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                data = message.get("bytes")
                if data is not None:
                    audio_bytes_total += len(data)
                    await session.send_audio(data)
                    continue
                text = message.get("text")
                if text:
                    try:
                        frame = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if frame.get("type") == "stop":
                        await session.finish()
                        return
                    if frame.get("type") == "cancel":
                        return
        except (WebSocketDisconnect, AliyunAsrError):
            return

    async def consume() -> None:
        async for result in session.results():
            text = result["text"]
            if result["sentence_end"]:
                if text.strip():
                    sentences.append(text)
                    await websocket.send_json({"type": "sentence", "text": text})
                    await voice_room_hub.broadcast_transcript(room.code, seq=seq, text=text)
            elif text:
                await websocket.send_json({"type": "partial", "text": text})

    try:
        await asyncio.gather(pump_phone(), consume())
    except AliyunAsrError as error:
        record_voice_log(db, room_id=room.id, seq=seq, kind="error", detail={"message": str(error)})
        db.commit()
        try:
            await websocket.send_json({"type": "error", "message": str(error)})
        except Exception:
            pass
    finally:
        await session.close()

    full_text = "".join(sentences).strip()
    if not full_text:
        return

    optimized = full_text
    llm_model = ""
    llm_latency_ms = None
    if config.llm_ready:
        import time

        llm_started = time.perf_counter()
        try:
            optimized = await polish_text(config, full_text)
            llm_latency_ms = int((time.perf_counter() - llm_started) * 1000)
            llm_model = config.llm_model
        except RuntimeError as error:
            record_voice_log(
                db,
                room_id=room.id,
                seq=seq,
                kind="error",
                raw_text=full_text,
                text=full_text,
                detail={"message": str(error)},
            )
            db.commit()

    message = VoiceMessage(
        room_id=room.id,
        seq=seq,
        raw_text=full_text,
        text=optimized,
        audio_size=audio_bytes_total,
        client_ip=client_ip,
        stt_model=config.stt_model,
        llm_model=llm_model,
        llm_latency_ms=llm_latency_ms,
    )
    db.add(message)
    record_voice_log(
        db,
        room_id=room.id,
        seq=seq,
        kind="transcribed",
        raw_text=full_text,
        text=optimized,
        detail={
            "audio_size": audio_bytes_total,
            "source_ip": client_ip,
            "stt_model": config.stt_model,
            "llm_model": llm_model,
            "llm_latency_ms": llm_latency_ms,
        },
    )
    db.commit()
    db.refresh(message)

    delivered = await voice_room_hub.broadcast_optimized(
        room.code,
        seq=seq,
        raw_text=full_text,
        text=optimized,
    )
    if delivered > 0:
        message.delivered_count = delivered
        record_voice_log(
            db,
            room_id=room.id,
            seq=seq,
            kind="sent",
            raw_text=full_text,
            text=optimized,
            detail={"delivered": delivered},
        )
        db.commit()
    try:
        await websocket.send_json({"type": "optimized", "seq": seq, "raw_text": full_text, "text": optimized, "delivered": delivered})
    except Exception:
        pass


@router.websocket("/ws/{room_code}")
async def voice_ws(room_code: str, websocket: WebSocket, db: Session = Depends(get_db)) -> None:
    """桌面端小程序：接收识别/优化文本，回传 ack 确认。"""
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

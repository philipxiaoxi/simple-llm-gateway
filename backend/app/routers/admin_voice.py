from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_admin
from app.models import Admin, VoiceRoom
from app.schemas import (
    VoiceRoomCreate,
    VoiceRoomOut,
    VoiceRoomUpdate,
    VoiceSettingsOut,
    VoiceSettingsUpdate,
)
from app.services.voice import (
    generate_room_code,
    room_payload,
    save_voice_config,
    settings_payload,
)

router = APIRouter(prefix="/api/admin/voice", tags=["admin-voice"], dependencies=[Depends(get_current_admin)])


@router.get("/rooms", response_model=list[VoiceRoomOut])
def list_rooms(db: Session = Depends(get_db)) -> list[VoiceRoomOut]:
    base_url = get_settings().app_base_url
    rooms = db.scalars(
        select(VoiceRoom).order_by(VoiceRoom.id.desc()).limit(100)
    ).all()
    return [VoiceRoomOut(**room_payload(db, room, base_url=base_url, include_messages=True)) for room in rooms]


@router.post("/rooms", response_model=VoiceRoomOut)
def create_room(
    payload: VoiceRoomCreate,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> VoiceRoomOut:
    base_url = get_settings().app_base_url
    room = VoiceRoom(
        code=generate_room_code(db),
        name=payload.name.strip() or "语音输入房间",
        status="active",
        created_by=admin.username,
    )
    db.add(room)
    db.commit()
    db.refresh(room)
    return VoiceRoomOut(**room_payload(db, room, base_url=base_url))


@router.patch("/rooms/{room_id}", response_model=VoiceRoomOut)
def update_room(
    room_id: int,
    payload: VoiceRoomUpdate,
    db: Session = Depends(get_db),
) -> VoiceRoomOut:
    room = db.get(VoiceRoom, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="房间不存在")
    if payload.name is not None:
        room.name = payload.name.strip() or room.name
    if payload.status is not None:
        if payload.status not in {"active", "closed"}:
            raise HTTPException(status_code=400, detail="status 只接受 active / closed")
        room.status = payload.status
    db.commit()
    db.refresh(room)
    return VoiceRoomOut(**room_payload(db, room, base_url=get_settings().app_base_url))


@router.delete("/rooms/{room_id}", response_model=dict[str, bool])
def delete_room(room_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    room = db.get(VoiceRoom, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="房间不存在")
    db.delete(room)
    db.commit()
    return {"ok": True}


@router.get("/settings", response_model=VoiceSettingsOut)
def get_settings_endpoint(db: Session = Depends(get_db)) -> VoiceSettingsOut:
    return VoiceSettingsOut(**settings_payload(db))


@router.put("/settings", response_model=VoiceSettingsOut)
def put_settings_endpoint(
    payload: VoiceSettingsUpdate,
    db: Session = Depends(get_db),
) -> VoiceSettingsOut:
    try:
        save_voice_config(
            db,
            stt_account_id=payload.stt_account_id,
            stt_model=payload.stt_model or "",
            stt_language=payload.stt_language,
            llm_account_id=payload.llm_account_id,
            llm_model=payload.llm_model or "",
            llm_prompt=payload.llm_prompt or "",
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return VoiceSettingsOut(**settings_payload(db))
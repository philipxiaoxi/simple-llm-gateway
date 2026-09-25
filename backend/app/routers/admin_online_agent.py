from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_admin
from app.services import online_agent

router = APIRouter(
    prefix="/api/admin/online-agent",
    tags=["admin-online-agent"],
    dependencies=[Depends(get_current_admin)],
)


class ToggleIn(BaseModel):
    target_id: str
    enabled: bool


class PromptIn(BaseModel):
    text: str
    model: str


class SelectionIn(BaseModel):
    model: str | None = None
    session_id: str | None = None


def _http(error: online_agent.OnlineAgentError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail={"error": {"message": error.message}})


@router.get("")
def get_state(db: Session = Depends(get_db)):
    return online_agent.state_payload(db)


@router.post("/start")
def start(db: Session = Depends(get_db)):
    try:
        online_agent.start(db)
        db.commit()
    except online_agent.OnlineAgentError as error:
        db.commit()
        raise _http(error) from error
    return online_agent.state_payload(db)


@router.post("/stop")
def stop(db: Session = Depends(get_db)):
    online_agent.stop(db)
    db.commit()
    return online_agent.state_payload(db)


@router.post("/restart")
def restart(db: Session = Depends(get_db)):
    try:
        online_agent.restart(db)
        db.commit()
    except online_agent.OnlineAgentError as error:
        db.commit()
        raise _http(error) from error
    return online_agent.state_payload(db)


@router.post("/sync")
def sync(db: Session = Depends(get_db)):
    online_agent.sync(db)
    db.commit()
    return online_agent.state_payload(db)


@router.put("/skills")
def set_skill(payload: ToggleIn, db: Session = Depends(get_db)):
    try:
        online_agent.set_toggle(db, "skill", payload.target_id, payload.enabled)
        db.commit()
    except online_agent.OnlineAgentError as error:
        raise _http(error) from error
    return online_agent.state_payload(db)


@router.put("/mcp")
def set_mcp(payload: ToggleIn, db: Session = Depends(get_db)):
    try:
        online_agent.set_toggle(db, "mcp", payload.target_id, payload.enabled)
        db.commit()
    except online_agent.OnlineAgentError as error:
        raise _http(error) from error
    return online_agent.state_payload(db)


@router.get("/models")
def models(db: Session = Depends(get_db)):
    return {"items": online_agent.list_models(db)}


@router.post("/selection")
def selection(payload: SelectionIn, db: Session = Depends(get_db)):
    online_agent.remember_selection(db, model=payload.model, session_id=payload.session_id)
    db.commit()
    return {"ok": True}


@router.get("/sessions")
def sessions(db: Session = Depends(get_db)):
    try:
        return {"items": online_agent.list_sessions(db)}
    except online_agent.OnlineAgentError as error:
        raise _http(error) from error


@router.post("/sessions")
def create_session(db: Session = Depends(get_db)):
    try:
        created = online_agent.create_session(db)
        db.commit()
    except online_agent.OnlineAgentError as error:
        raise _http(error) from error
    return created


@router.get("/sessions/{session_id}/messages")
def messages(session_id: str):
    try:
        return {"items": online_agent.session_messages(session_id)}
    except online_agent.OnlineAgentError as error:
        raise _http(error) from error


@router.post("/sessions/{session_id}/prompt")
def prompt(session_id: str, payload: PromptIn, db: Session = Depends(get_db)):
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail={"error": {"message": "消息不能为空"}})
    if not payload.model:
        raise HTTPException(status_code=400, detail={"error": {"message": "请选择模型"}})
    try:
        result = online_agent.prompt(db, session_id, payload.text.strip(), payload.model)
        db.commit()
    except online_agent.OnlineAgentError as error:
        raise _http(error) from error
    return result


@router.post("/sessions/{session_id}/compact")
def compact(session_id: str):
    try:
        return online_agent.compact(session_id)
    except online_agent.OnlineAgentError as error:
        raise _http(error) from error


@router.post("/sessions/{session_id}/reset")
def reset(session_id: str, db: Session = Depends(get_db)):
    try:
        created = online_agent.reset_session(db, session_id)
        db.commit()
    except online_agent.OnlineAgentError as error:
        raise _http(error) from error
    return created


@router.get("/events")
def events():
    if not online_agent.process_running():
        raise HTTPException(status_code=503, detail={"error": {"message": "OpenCode 未启动"}})

    def stream():
        with httpx.stream("GET", f"{online_agent.opencode_base()}/event", timeout=None) as response:
            for line in response.iter_lines():
                yield f"{line}\n"

    return StreamingResponse(stream(), media_type="text/event-stream")

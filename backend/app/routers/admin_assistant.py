from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.assistant.agent import run_turn
from app.assistant.api_catalog import get_operation, page_directory
from app.assistant.api_client import execute_api
from app.assistant.links import public_origin_from_request, rewrite_public_links
from app.assistant.mcp_bridge import invoke_for_admin
from app.assistant.store import (
    append_message,
    approval_payload,
    claim_approval,
    create_conversation,
    delete_conversation,
    get_approval,
    get_conversation,
    list_conversations,
    mark_approval,
    model_options,
    pending_approval,
    rename_conversation,
    settings_payload,
    update_settings,
)
from app.assistant.tools import build_tool_defs, llm_result
from app.capabilities.base import CapabilityError
from app.db import get_db, get_session_factory
from app.deps import get_current_admin
from app.models import Admin, AssistantApproval
from app.services.permission_guard import permissions_for_admin

router = APIRouter(
    prefix="/api/admin/assistant",
    tags=["admin-assistant"],
    dependencies=[Depends(get_current_admin)],
)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _error(message: str, status_code: int = 400) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"type": "invalid_request", "message": message}},
    )


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode()


def _request_token(request: Request) -> str:
    value = request.headers.get("authorization", "")
    if value.lower().startswith("bearer "):
        return value.split(" ", 1)[1].strip()
    raise HTTPException(
        status_code=401,
        detail={"error": {"type": "authentication_error", "message": "缺少登录令牌"}},
    )


async def _next_event(iterator):
    try:
        return await iterator.__anext__()
    except StopAsyncIteration:
        return None


async def _with_heartbeat(events, interval: float = 10.0):
    iterator = events.__aiter__()
    pending = asyncio.ensure_future(_next_event(iterator))
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield b": ping\n\n"
                continue
            item = pending.result()
            if item is None:
                break
            yield _sse(item[0], item[1])
            pending = asyncio.ensure_future(_next_event(iterator))
    finally:
        pending.cancel()
        with suppress(asyncio.CancelledError):
            await pending
        close = getattr(iterator, "aclose", None)
        if close is not None:
            with suppress(Exception):
                await close()


@router.get("/settings")
def get_assistant_settings(db: Session = Depends(get_db)):
    return settings_payload(db)


@router.put("/settings")
def put_assistant_settings(payload: dict | None = None, db: Session = Depends(get_db)):
    try:
        result = update_settings(db, payload or {})
        db.commit()
        return result
    except ValueError as error:
        db.rollback()
        raise _error(str(error)) from error


@router.get("/models")
def get_assistant_models(db: Session = Depends(get_db)):
    return {"items": model_options(db)}


@router.get("/tools")
def get_assistant_tools(
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    permissions = permissions_for_admin(db, admin)
    return {
        "items": [
            {
                "name": item["function"]["name"],
                "description": item["function"].get("description", ""),
            }
            for item in build_tool_defs()
        ],
        "pages": page_directory(permissions),
    }


@router.get("/conversations")
def get_conversations(
    limit: int = 50,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return {"items": list_conversations(db, admin.id, limit=limit)}


@router.post("/conversations", status_code=201)
def post_conversation(
    payload: dict | None = None,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    title = str((payload or {}).get("title") or "").strip() or None
    row = create_conversation(db, admin.id, title=title)
    db.commit()
    return {
        "id": row.id,
        "title": row.title,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _owned_conversation(db: Session, admin: Admin, conversation_id: str):
    row = get_conversation(db, admin.id, conversation_id)
    if row is None:
        raise HTTPException(404, detail={"error": {"type": "not_found", "message": "会话不存在"}})
    return row


@router.get("/conversations/{conversation_id}")
def get_conversation_detail(
    conversation_id: str,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    from app.assistant.store import conversation_payload

    row = _owned_conversation(db, admin, conversation_id)
    return conversation_payload(db, row)


@router.patch("/conversations/{conversation_id}")
def patch_conversation(
    conversation_id: str,
    payload: dict | None = None,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    row = _owned_conversation(db, admin, conversation_id)
    try:
        rename_conversation(db, row, str((payload or {}).get("title") or ""))
        db.commit()
    except ValueError as error:
        db.rollback()
        raise _error(str(error)) from error
    return {
        "id": row.id,
        "title": row.title,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.delete("/conversations/{conversation_id}", status_code=204)
def remove_conversation(
    conversation_id: str,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    row = _owned_conversation(db, admin, conversation_id)
    delete_conversation(db, row)
    db.commit()


@router.post("/conversations/{conversation_id}/messages")
async def post_message(
    conversation_id: str,
    request: Request,
    payload: dict | None = None,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    body = payload or {}
    content = str(body.get("content") or "").strip()
    if not content:
        raise _error("消息不能为空")
    if len(content) > 8000:
        raise _error("消息不能超过 8000 个字符")
    _owned_conversation(db, admin, conversation_id)
    if pending_approval(db, conversation_id) is not None:
        raise HTTPException(
            status_code=409,
            detail={"error": {"type": "approval_pending", "message": "请先处理当前待审批操作"}},
        )
    settings = settings_payload(db)
    if not settings.get("configured"):
        raise HTTPException(
            status_code=409,
            detail={"error": {"type": "not_configured", "message": "助手尚未配置可用的对话模型"}},
        )
    page = body.get("page")
    page_context = page if isinstance(page, dict) else None
    admin_id = int(admin.id)
    admin_name = str(admin.username)
    token = _request_token(request)
    public_origin = public_origin_from_request(request)

    async def stream():
        async for chunk in _with_heartbeat(
            run_turn(
                admin_id=admin_id,
                admin_name=admin_name,
                conversation_id=conversation_id,
                user_text=content,
                page=page_context,
                is_disconnected=request.is_disconnected,
                token=token,
                app=request.app,
                continuation=False,
                public_origin=public_origin,
            )
        ):
            yield chunk

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/approvals/{approval_id}/decision")
async def decide_approval(
    approval_id: str,
    request: Request,
    payload: dict | None = None,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    body = payload or {}
    decision = str(body.get("decision") or "").strip()
    if decision not in {"approve", "reject"}:
        raise _error("decision 必须是 approve 或 reject")
    row = get_approval(db, admin.id, approval_id)
    if row is None:
        raise HTTPException(404, detail={"error": {"type": "not_found", "message": "审批不存在"}})
    if row.status != "pending":
        raise HTTPException(
            status_code=409,
            detail={"error": {"type": "approval_done", "message": "该操作已经处理过"}},
        )
    conversation = get_conversation(db, admin.id, row.conversation_id)
    if conversation is None:
        raise HTTPException(404, detail={"error": {"type": "not_found", "message": "会话不存在"}})
    if not claim_approval(db, row, decision):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"error": {"type": "approval_done", "message": "该操作已经处理过"}},
        )
    db.commit()

    admin_id = int(admin.id)
    admin_name = str(admin.username)
    conversation_id = row.conversation_id
    tool_call_id = row.tool_call_id
    api_id = row.api_id
    tool_name = "mcp_call" if api_id.startswith("MCP ") else "api_call"
    method = row.method
    path = row.path
    summary = row.summary
    reason = row.reason
    path_params = json.loads(row.path_params_json or "{}")
    query = json.loads(row.query_json or "{}")
    request_body = json.loads(row.body_json) if row.body_json else None
    page = body.get("page")
    page_context = page if isinstance(page, dict) else None
    token = _request_token(request)
    app = request.app
    public_origin = public_origin_from_request(request)

    started = time.perf_counter()
    ok = False
    result_payload: object = None
    error_message = ""
    latency_ms = 0
    session = get_session_factory()()
    try:
        approval_row = session.get(AssistantApproval, approval_id)
        if approval_row is None:
            raise RuntimeError("审批记录不存在")
        if decision == "reject":
            error_message = "用户拒绝执行"
            tool_content = json.dumps(
                {"error": error_message, "code": "user_rejected"}, ensure_ascii=False
            )
            mark_approval(session, approval_row, status="rejected", error=error_message)
        else:
            if tool_name == "mcp_call":
                try:
                    result_payload = await invoke_for_admin(
                        session,
                        admin=admin,
                        tool_name=api_id[4:].strip(),
                        payload=request_body if isinstance(request_body, dict) else {},
                    )
                    result_payload = rewrite_public_links(result_payload, public_origin)
                    ok = True
                    error_message = ""
                    tool_content = llm_result(result_payload)
                except CapabilityError as error:
                    ok = False
                    result_payload = None
                    error_message = error.message
                    tool_content = json.dumps({"error": error_message}, ensure_ascii=False)
                latency_ms = int((time.perf_counter() - started) * 1000)
                mark_approval(
                    session,
                    approval_row,
                    status="executed" if ok else "failed",
                    result=result_payload,
                    error=error_message,
                )
            else:
                operation = get_operation(app, api_id)
                if operation is None:
                    error_message = "接口不存在或已下线"
                    tool_content = json.dumps({"error": error_message}, ensure_ascii=False)
                    mark_approval(session, approval_row, status="failed", error=error_message)
                elif not operation.supported:
                    error_message = "该接口需要文件或表单上传，请在对应页面操作"
                    tool_content = json.dumps({"error": error_message}, ensure_ascii=False)
                    mark_approval(session, approval_row, status="failed", error=error_message)
                else:
                    outcome = await execute_api(
                        app,
                        token,
                        operation,
                        path_params=path_params,
                        query=query,
                        body=request_body,
                        timeout_seconds=120.0,
                    )
                    ok = bool(outcome.get("ok"))
                    result_payload = rewrite_public_links(outcome.get("data"), public_origin) if ok else None
                    error_message = str(outcome.get("error") or "")
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    tool_content = (
                        llm_result(result_payload)
                        if ok
                        else json.dumps({"error": error_message or "执行失败"}, ensure_ascii=False)
                    )
                    mark_approval(
                        session,
                        approval_row,
                        status="executed" if ok else "failed",
                        result=result_payload,
                        error=error_message,
                    )
        append_message(
            session,
            conversation_id,
            "tool",
            tool_content,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            meta={
                "ok": ok,
                "result": result_payload,
                "summary": summary if ok else (error_message or "执行失败"),
                "error": error_message,
                "latency_ms": latency_ms,
                "kind": "approval",
            },
        )
        session.commit()
    except Exception as error:  # noqa: BLE001
        session.rollback()
        ok = False
        result_payload = None
        error_message = f"审批执行失败：{error}"
        try:
            fallback = get_session_factory()()
            try:
                approval_row = fallback.get(AssistantApproval, approval_id)
                if approval_row is not None:
                    mark_approval(fallback, approval_row, status="failed", error=error_message)
                append_message(
                    fallback,
                    conversation_id,
                    "tool",
                    json.dumps({"error": error_message}, ensure_ascii=False),
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    meta={"ok": False, "result": None, "summary": error_message, "error": error_message, "latency_ms": 0},
                )
                fallback.commit()
            finally:
                fallback.close()
        except Exception:  # noqa: BLE001
            pass
    finally:
        session.close()

    tool_result_payload = {
        "id": tool_call_id,
        "name": tool_name,
        "ok": ok,
        "summary": summary if ok else (error_message or "执行失败"),
        "result": result_payload,
        "error": error_message,
        "latency_ms": latency_ms,
        "kind": "approval",
        "api_id": api_id,
        "method": method,
        "path": path,
        "reason": reason,
    }

    async def events():
        yield "tool_result", tool_result_payload
        async for item in run_turn(
            admin_id=admin_id,
            admin_name=admin_name,
            conversation_id=conversation_id,
            user_text=None,
            page=page_context,
            is_disconnected=request.is_disconnected,
            token=token,
            app=app,
            continuation=True,
            public_origin=public_origin,
        ):
            yield item

    return StreamingResponse(_with_heartbeat(events()), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/approvals/{approval_id}")
def get_approval_detail(
    approval_id: str,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    row = get_approval(db, admin.id, approval_id)
    if row is None:
        raise HTTPException(404, detail={"error": {"type": "not_found", "message": "审批不存在"}})
    return approval_payload(row)

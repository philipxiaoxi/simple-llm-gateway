import json
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, StrictBool
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, load_only

from app.db import get_db
from app.deps import get_current_admin
from app.models import RequestLog
from app.schemas import (
    LogExportOut,
    LogListOut,
    LogMessageDetailOut,
    LogMessageListOut,
    LogMessageOut,
    LogOut,
    LogReasoningDetailOut,
    LogTurnListOut,
    LogTurnOut,
    ReasoningSummaryItemOut,
)
from app.serializers import build_model_chain, log_to_out
from app.services.conversation import (
    MAX_LOG_MESSAGES,
    content_preview,
    decode_stored_message,
    tool_calls_summary,
)
from app.services.log_content import dropped_content, read_content
from app.services.log_content_cleanup import cleanup_pause, cleanup_start, cleanup_status
from app.services.log_content_migration import (
    legacy_content_archived,
    migration_pause,
    migration_start,
    migration_status,
)
from app.services.permission_guard import require_permission
from app.services.proxy import parse_json
from app.services.reasoning import parse_reasoning_json, summarize_reasoning

router = APIRouter(prefix="/api/admin/logs", tags=["admin-logs"], dependencies=[Depends(get_current_admin)])

# 列表接口实际用到的列；正文与 reasoning 只在详情接口读取
_LIST_COLUMNS = (
    RequestLog.id,
    RequestLog.account_id,
    RequestLog.account_name,
    RequestLog.account_source,
    RequestLog.api_key_id,
    RequestLog.api_key_name,
    RequestLog.protocol,
    RequestLog.model,
    RequestLog.stream,
    RequestLog.status,
    RequestLog.http_status,
    RequestLog.error_message,
    RequestLog.prompt_tokens,
    RequestLog.completion_tokens,
    RequestLog.total_tokens,
    RequestLog.latency_ms,
    RequestLog.last_latency_ms,
    RequestLog.turn_count,
    RequestLog.client_ip,
    RequestLog.ip_location,
    RequestLog.user_agent,
    RequestLog.client_name,
    RequestLog.created_at,
    RequestLog.updated_at,
)


@router.get("", response_model=LogListOut)
def list_logs(
    account_id: int | None = None,
    api_key_id: int | None = None,
    protocol: str | None = None,
    status: str | None = None,
    model: str | None = None,
    q: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> LogListOut:
    filters = select(RequestLog)
    if account_id is not None:
        filters = filters.where(RequestLog.account_id == account_id)
    if api_key_id is not None:
        filters = filters.where(RequestLog.api_key_id == api_key_id)
    if protocol:
        filters = filters.where(RequestLog.protocol == protocol)
    if status:
        filters = filters.where(RequestLog.status == status)
    if model:
        filters = filters.where(RequestLog.model == model)
    if q:
        keyword = q.strip()
        if keyword:
            like = f"%{keyword}%"
            filters = filters.where(
                or_(
                    RequestLog.model.like(like),
                    RequestLog.api_key_name.like(like),
                    RequestLog.account_name.like(like),
                    RequestLog.session_key.like(like),
                )
            )
    if since:
        filters = filters.where(RequestLog.created_at >= since)
    if until:
        filters = filters.where(RequestLog.created_at <= until)
    total = db.scalar(select(func.count()).select_from(filters.with_only_columns(RequestLog.id).subquery())) or 0
    offset = (page - 1) * page_size
    rows = db.scalars(
        filters.options(
            # 列表不返回正文，别把 request_body/response_body/reasoning_json 读出来
            load_only(*_LIST_COLUMNS),
            joinedload(RequestLog.api_key),
            joinedload(RequestLog.account),
        )
        .order_by(RequestLog.updated_at.desc(), RequestLog.id.desc())
        .offset(offset)
        .limit(page_size)
    ).unique().all()
    items = [
        log_to_out(row, turn_count=row.turn_count if row.turn_count is not None else 1)
        for row in rows
    ]
    return LogListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/content-migration", dependencies=[Depends(require_permission("*"))])
def get_content_migration() -> dict:
    return {**migration_status(), "dropped_content": dropped_content()}


@router.post("/content-migration/start", dependencies=[Depends(require_permission("*"))])
def start_content_migration() -> dict:
    return {**migration_start(), "dropped_content": dropped_content()}


@router.post("/content-migration/scan", dependencies=[Depends(require_permission("*"))])
def scan_content_migration() -> dict:
    return {**migration_start(scan_only=True), "dropped_content": dropped_content()}


@router.post("/content-migration/pause", dependencies=[Depends(require_permission("*"))])
def pause_content_migration() -> dict:
    return {**migration_pause(), "dropped_content": dropped_content()}


class CleanupConfirmation(BaseModel):
    backup_confirmed: StrictBool


@router.get("/content-cleanup", dependencies=[Depends(require_permission("*"))])
def get_content_cleanup() -> dict:
    return cleanup_status()


@router.post("/content-cleanup/start", dependencies=[Depends(require_permission("*"))])
def start_content_cleanup(confirmation: CleanupConfirmation) -> dict:
    if not confirmation.backup_confirmed:
        raise HTTPException(status_code=422, detail="请先确认已备份主库和正文归档，并验证备份可恢复。")
    try:
        return cleanup_start()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/content-cleanup/pause", dependencies=[Depends(require_permission("*"))])
def pause_content_cleanup() -> dict:
    return cleanup_pause()


@contextmanager
def _log_content(db: Session, log_id: int):
    if db.scalar(select(RequestLog.id).where(RequestLog.id == log_id)) is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    with read_content(archived=legacy_content_archived()) as (content, details, legacy, live):
        # A reserved sequence range prevents collisions when a legacy session receives a new turn.
        messages = f"SELECT seq, role, content_json FROM {legacy} WHERE log_id = :log_id"
        if live:
            messages += f" UNION ALL SELECT seq, role, content_json FROM {live}.live_log_messages WHERE log_id = :log_id"

        def query(columns, suffix="", **params):
            return content.execute(f"SELECT {columns} FROM ({messages}) {suffix}", {"log_id": log_id, **params})

        def detail(include_bodies=False):
            columns = "request_headers, reasoning_json"
            if include_bodies:
                columns += ", request_body, response_body"
            key = "id" if details == "request_logs" else "log_id"
            old = content.execute(f"SELECT {columns} FROM {details} WHERE {key} = ?", (log_id,)).fetchone()
            result = dict(old) if old else {}
            if live:
                new = content.execute(f"SELECT request_headers, reasoning_json FROM {live}.live_log_content "
                                      "WHERE log_id = ?", (log_id,)).fetchone()
                if new:
                    result["request_headers"] = new["request_headers"] or result.get("request_headers")
                    result["reasoning_json"] = json.dumps({
                        **parse_reasoning_json(result.get("reasoning_json")),
                        **parse_reasoning_json(new["reasoning_json"]),
                    })
            return result

        yield query, detail


@router.get("/{log_id}/messages", response_model=LogMessageListOut)
def list_log_messages(
    log_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    from_seq: int | None = None,
    to_seq: int | None = None,
    detail: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> LogMessageListOut:
    conditions = []
    params = {}
    if from_seq is not None:
        conditions.append("seq >= :from_seq")
        params["from_seq"] = from_seq
    if to_seq is not None:
        conditions.append("seq <= :to_seq")
        params["to_seq"] = to_seq
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    with _log_content(db, log_id) as (query, _):
        total = query("count(*)", where, **params).fetchone()[0]
        rows = query("seq, role, content_json", where + " ORDER BY seq DESC LIMIT :limit OFFSET :offset",
                     **params, limit=page_size, offset=(page - 1) * page_size).fetchall()
    items: list[LogMessageOut] = []
    for row in rows:
        decoded = decode_stored_message(SimpleNamespace(**row))
        preview, truncated, length = content_preview(decoded.get("content"))
        items.append(
            LogMessageOut(
                seq=row["seq"],
                role=decoded["role"],
                content_preview=preview,
                content_truncated=truncated,
                content_length=length,
                tool_calls=tool_calls_summary(decoded.get("tool_calls")),
                # 仅轮次弹窗等范围查询按需携带全文，列表默认不传大正文
                content=decoded.get("content") if detail else None,
                tool_calls_full=decoded.get("tool_calls") if detail else None,
            )
        )
    return LogMessageListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/{log_id}/turns", response_model=LogTurnListOut)
def list_log_turns(log_id: int, db: Session = Depends(get_db)) -> LogTurnListOut:
    """轮次轮廓：仅按 seq/role 划分轮次边界，不读正文，供折叠列表与弹窗定位使用。"""
    with _log_content(db, log_id) as (query, _):
        rows = query("seq, role", "ORDER BY seq").fetchall()
    items: list[LogTurnOut] = []
    seqs = [seq for seq, _ in rows]
    anchors: list[int] = []
    index_by_anchor: dict[int, int] = {}
    counter = 0
    for seq, role in rows:
        if role == "user":
            counter += 1
            anchors.append(seq)
            index_by_anchor[seq] = counter
    if not rows:
        return LogTurnListOut(items=items)
    if not anchors:
        items.append(LogTurnOut(index=None, anchor_seq=seqs[0], to_seq=seqs[-1], message_count=len(seqs)))
        return LogTurnListOut(items=items)
    prefix = [seq for seq in seqs if seq < anchors[0]]
    if prefix:
        items.append(
            LogTurnOut(index=None, anchor_seq=prefix[0], to_seq=prefix[-1], message_count=len(prefix))
        )
    for position, anchor in enumerate(anchors):
        to_seq = anchors[position + 1] - 1 if position + 1 < len(anchors) else seqs[-1]
        count = sum(1 for seq in seqs if seq >= anchor and seq <= to_seq)
        items.append(
            LogTurnOut(
                index=index_by_anchor[anchor],
                anchor_seq=anchor,
                to_seq=to_seq,
                message_count=count,
            )
        )
    return LogTurnListOut(items=items)


@router.get("/{log_id}/messages/{seq}", response_model=LogMessageDetailOut)
def get_log_message(log_id: int, seq: int, db: Session = Depends(get_db)) -> LogMessageDetailOut:
    with _log_content(db, log_id) as (query, _):
        row = query("seq, role, content_json", "WHERE seq = :seq", seq=seq).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    decoded = decode_stored_message(SimpleNamespace(**row))
    return LogMessageDetailOut(
        seq=row["seq"],
        role=decoded["role"],
        content=decoded.get("content"),
        tool_calls=decoded.get("tool_calls"),
    )


@router.get("/{log_id}/reasoning/{call_id}", response_model=LogReasoningDetailOut)
def get_log_reasoning(log_id: int, call_id: str, db: Session = Depends(get_db)) -> LogReasoningDetailOut:
    with _log_content(db, log_id) as (_, detail):
        mapping = parse_reasoning_json(detail().get("reasoning_json"))
    text = mapping.get(call_id)
    if text is None:
        raise HTTPException(status_code=404, detail="思考内容不存在")
    return LogReasoningDetailOut(call_id=call_id, text=text)


@router.get("/{log_id}/export", response_model=LogExportOut)
def export_log_messages(log_id: int, db: Session = Depends(get_db)) -> LogExportOut:
    """一次性导出会话全部消息与思考内容，供前端下载 JSON（上限受存储保留策略约束）。"""
    with _log_content(db, log_id) as (query, detail):
        rows = query("seq, role, content_json", "ORDER BY seq").fetchall()
        reasoning = parse_reasoning_json(detail().get("reasoning_json"))
    messages: list[LogMessageDetailOut] = []
    for row in rows:
        decoded = decode_stored_message(SimpleNamespace(**row))
        messages.append(
            LogMessageDetailOut(
                seq=row["seq"],
                role=decoded["role"],
                content=decoded.get("content"),
                tool_calls=decoded.get("tool_calls"),
            )
        )
    return LogExportOut(messages=messages, reasoning=reasoning)


@router.get("/{log_id}", response_model=LogOut)
def get_log(
    log_id: int,
    include_bodies: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> LogOut:
    item = db.scalar(select(RequestLog).where(RequestLog.id == log_id).options(load_only(*_LIST_COLUMNS)))
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    with _log_content(db, log_id) as (query, detail):
        message_count, turn_count = query("count(*), count(CASE WHEN role = 'assistant' THEN 1 END)").fetchone()
        payload = detail(include_bodies)
    reasoning = summarize_reasoning(parse_reasoning_json(payload.get("reasoning_json")))
    reasoning_summary = (
        {call_id: ReasoningSummaryItemOut(**payload) for call_id, payload in reasoning.items()}
        if reasoning
        else None
    )
    return log_to_out(
        item,
        message_count=message_count,
        turn_count=item.turn_count if item.turn_count is not None else turn_count,
        reasoning_summary=reasoning_summary,
        model_chain=build_model_chain(item),
        storage_truncated=message_count >= MAX_LOG_MESSAGES,
        request_headers=_load_headers(payload.get("request_headers")),
    ).model_copy(update={
        "request_body": parse_json(payload.get("request_body")),
        "response_body": parse_json(payload.get("response_body")),
    })


def _load_headers(raw: str | None) -> dict[str, str] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return {str(key): str(value) for key, value in parsed.items()}

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, joinedload, load_only

from app.db import get_db
from app.deps import get_current_admin
from app.models import RequestLog, RequestLogMessage
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
    total = db.scalar(select(func.count()).select_from(filters.subquery())) or 0
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
    # 会话聚合统计：消息总数与模型调用轮次，按本页 log 一次性聚合，避免 N+1
    counts: dict[int, tuple[int, int]] = {}
    ids = [row.id for row in rows]
    if ids:
        for log_id, message_count, turn_count in db.execute(
            select(
                RequestLogMessage.log_id,
                func.count(),
                func.sum(case((RequestLogMessage.role == "assistant", 1), else_=0)),
            )
            .where(RequestLogMessage.log_id.in_(ids))
            .group_by(RequestLogMessage.log_id)
        ).all():
            counts[log_id] = (int(message_count or 0), int(turn_count or 0))
    items = [
        log_to_out(
            row,
            message_count=counts.get(row.id, (0, 0))[0],
            turn_count=counts.get(row.id, (0, 0))[1],
        )
        for row in rows
    ]
    return LogListOut(items=items, total=total, page=page, page_size=page_size)


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
    item = db.get(RequestLog, log_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    filters = select(RequestLogMessage).where(RequestLogMessage.log_id == log_id)
    if from_seq is not None:
        filters = filters.where(RequestLogMessage.seq >= from_seq)
    if to_seq is not None:
        filters = filters.where(RequestLogMessage.seq <= to_seq)
    total = db.scalar(select(func.count()).select_from(filters.subquery())) or 0
    offset = (page - 1) * page_size
    columns = [RequestLogMessage.seq, RequestLogMessage.role, RequestLogMessage.content_json]
    rows = db.scalars(
        filters.options(load_only(*columns))
        .order_by(RequestLogMessage.seq.desc())
        .offset(offset)
        .limit(page_size)
    ).all()
    items: list[LogMessageOut] = []
    for row in rows:
        decoded = decode_stored_message(row)
        preview, truncated, length = content_preview(decoded.get("content"))
        items.append(
            LogMessageOut(
                seq=row.seq,
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
    item = db.get(RequestLog, log_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    rows = db.execute(
        select(RequestLogMessage.seq, RequestLogMessage.role)
        .where(RequestLogMessage.log_id == log_id)
        .order_by(RequestLogMessage.seq)
    ).all()
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
    item = db.get(RequestLog, log_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    row = db.scalar(
        select(RequestLogMessage)
        .where(RequestLogMessage.log_id == log_id, RequestLogMessage.seq == seq)
        .options(load_only(RequestLogMessage.seq, RequestLogMessage.role, RequestLogMessage.content_json))
    )
    if row is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    decoded = decode_stored_message(row)
    return LogMessageDetailOut(
        seq=row.seq,
        role=decoded["role"],
        content=decoded.get("content"),
        tool_calls=decoded.get("tool_calls"),
    )


@router.get("/{log_id}/reasoning/{call_id}", response_model=LogReasoningDetailOut)
def get_log_reasoning(log_id: int, call_id: str, db: Session = Depends(get_db)) -> LogReasoningDetailOut:
    item = db.scalar(
        select(RequestLog)
        .where(RequestLog.id == log_id)
        .options(load_only(RequestLog.id, RequestLog.reasoning_json))
    )
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    mapping = parse_reasoning_json(item.reasoning_json)
    text = mapping.get(call_id)
    if text is None:
        raise HTTPException(status_code=404, detail="思考内容不存在")
    return LogReasoningDetailOut(call_id=call_id, text=text)


@router.get("/{log_id}/export", response_model=LogExportOut)
def export_log_messages(log_id: int, db: Session = Depends(get_db)) -> LogExportOut:
    """一次性导出会话全部消息与思考内容，供前端下载 JSON（上限受存储保留策略约束）。"""
    item = db.get(RequestLog, log_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    rows = db.scalars(
        select(RequestLogMessage)
        .where(RequestLogMessage.log_id == log_id)
        .options(load_only(RequestLogMessage.seq, RequestLogMessage.role, RequestLogMessage.content_json))
        .order_by(RequestLogMessage.seq)
    ).all()
    messages: list[LogMessageDetailOut] = []
    for row in rows:
        decoded = decode_stored_message(row)
        messages.append(
            LogMessageDetailOut(
                seq=row.seq,
                role=decoded["role"],
                content=decoded.get("content"),
                tool_calls=decoded.get("tool_calls"),
            )
        )
    return LogExportOut(messages=messages, reasoning=parse_reasoning_json(item.reasoning_json))


@router.get("/{log_id}", response_model=LogOut)
def get_log(
    log_id: int,
    include_bodies: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> LogOut:
    columns = list(_LIST_COLUMNS)
    if include_bodies:
        columns.extend([RequestLog.request_body, RequestLog.response_body, RequestLog.reasoning_json])
    else:
        columns.append(RequestLog.reasoning_json)
    item = db.scalar(select(RequestLog).where(RequestLog.id == log_id).options(load_only(*columns)))
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    message_count = db.scalar(select(func.count()).where(RequestLogMessage.log_id == log_id)) or 0
    turn_count = (
        db.scalar(
            select(func.count()).where(
                RequestLogMessage.log_id == log_id,
                RequestLogMessage.role == "assistant",
            )
        )
        or 0
    )
    reasoning = summarize_reasoning(parse_reasoning_json(item.reasoning_json))
    reasoning_summary = (
        {call_id: ReasoningSummaryItemOut(**payload) for call_id, payload in reasoning.items()}
        if reasoning
        else None
    )
    return log_to_out(
        item,
        include_bodies=include_bodies,
        message_count=message_count,
        turn_count=turn_count,
        reasoning_summary=reasoning_summary,
        model_chain=build_model_chain(item),
        storage_truncated=message_count >= MAX_LOG_MESSAGES,
    )

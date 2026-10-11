from __future__ import annotations

import asyncio
import csv
import io
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.deps import get_current_admin
from app.models import Admin, BenchmarkResult, BenchmarkRun, UpstreamAccount
from app.providers import get_provider
from app.services.benchmark import (
    chunk_to_dict,
    compute_tokens_per_second,
    extract_answer_text,
    is_token_delta,
    output_tokens_from_chunk,
)
from app.services.bridge import ensure_stream_usage
from app.services.credentials import CredentialError, require_upstream_credential
from app.services.model_caps import parse_model_records

router = APIRouter(prefix="/api/admin/benchmark", tags=["admin-benchmark"], dependencies=[Depends(get_current_admin)])

TOTAL_TIMEOUT_SECONDS = 60


class BenchmarkRequest(BaseModel):
    account_id: int
    model: str = Field(min_length=1, max_length=200)
    prompt: str = Field(default="用一句话介绍你自己。", min_length=1, max_length=2000)
    max_tokens: int = Field(default=64, ge=1, le=512)


def timeout_result(account: UpstreamAccount, model: str) -> dict[str, Any]:
    return {
        "ok": False,
        "timeout": True,
        "account_id": account.id,
        "account_name": account.name,
        "provider": account.provider,
        "model": model,
        "error": f"测速超过 {TOTAL_TIMEOUT_SECONDS} 秒",
    }


@router.post("")
async def benchmark(payload: BenchmarkRequest, db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)) -> dict[str, Any]:
    account = db.scalar(
        select(UpstreamAccount)
        .options(joinedload(UpstreamAccount.oauth_token))
        .where(UpstreamAccount.id == payload.account_id)
    )
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if account.status != "active":
        raise HTTPException(status_code=400, detail="账号已停用")

    try:
        token = "agent-managed" if account.source == "agent" else require_upstream_credential(account)
        provider = get_provider(account.provider)
        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                provider.complete(
                    account,
                    [{"role": "user", "content": payload.prompt}],
                    payload.model,
                    True,
                    ensure_stream_usage({"max_tokens": payload.max_tokens}),
                    token,
                ),
                timeout=TOTAL_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return timeout_result(account, payload.model)
        first_token_ms: float | None = None
        output_text = ""
        output_tokens: int | None = None
        response_iterator = response.__aiter__()
        while True:
            remaining = TOTAL_TIMEOUT_SECONDS - (time.perf_counter() - started)
            if remaining <= 0:
                return timeout_result(account, payload.model)
            try:
                chunk = await asyncio.wait_for(response_iterator.__anext__(), timeout=remaining)
            except TimeoutError:
                return timeout_result(account, payload.model)
            except StopAsyncIteration:
                break

            payload_chunk = chunk_to_dict(chunk)
            usage_tokens = output_tokens_from_chunk(payload_chunk)
            if usage_tokens is not None:
                output_tokens = usage_tokens
            output_text += extract_answer_text(payload_chunk)
            if first_token_ms is None and is_token_delta(payload_chunk):
                first_token_ms = (time.perf_counter() - started) * 1000

        total_ms = (time.perf_counter() - started) * 1000
        reported_first_token_ms = None if first_token_ms is None else round(first_token_ms, 1)
        reported_total_ms = round(total_ms, 1)
        decode_ms = None if reported_first_token_ms is None else reported_total_ms - reported_first_token_ms
        output_chars = len(output_text)
        estimated_tokens = output_tokens if output_tokens is not None else max(0, round(output_chars / 4))
        return {
            "ok": True,
            "account_id": account.id,
            "account_name": account.name,
            "provider": account.provider,
            "model": payload.model,
            "first_token_ms": reported_first_token_ms,
            "total_ms": reported_total_ms,
            "output_chars": output_chars,
            "estimated_output_tokens": estimated_tokens,
            "output_tokens_per_second": compute_tokens_per_second(output_tokens, decode_ms),
            "preview": output_text[:500],
        }
    except CredentialError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except Exception as error:
        return {
            "ok": False,
            "account_id": account.id,
            "account_name": account.name,
            "provider": account.provider,
            "model": payload.model,
            "error": str(error)[:500],
        }


def _leaderboard_rows(db: Session, limit: int) -> list[Any]:
    avg_speed = func.avg(BenchmarkResult.output_tokens_per_second)
    return list(
        db.execute(
            select(
                BenchmarkResult.account_id,
                BenchmarkResult.account_name,
                BenchmarkResult.provider,
                BenchmarkResult.model,
                avg_speed.label("avg_speed"),
                func.max(BenchmarkResult.output_tokens_per_second).label("best_speed"),
                func.count().label("samples"),
                func.avg(BenchmarkResult.first_token_ms).label("avg_first_token_ms"),
                func.avg(BenchmarkResult.total_ms).label("avg_total_ms"),
                func.max(BenchmarkRun.created_at).label("last_tested_at"),
            )
            .join(BenchmarkRun, BenchmarkResult.run_id == BenchmarkRun.id)
            .where(BenchmarkResult.ok.is_(True), BenchmarkResult.output_tokens_per_second.is_not(None))
            .group_by(
                BenchmarkResult.account_id,
                BenchmarkResult.account_name,
                BenchmarkResult.provider,
                BenchmarkResult.model,
            )
            .order_by(avg_speed.desc())
            .limit(limit)
        ).all()
    )


def _valid_leaderboard_rows(db: Session, rows: list[Any]) -> list[Any]:
    """只保留账号仍存在、且模型仍在账号模型清单中的行；已删除/不存在的账号或模型不参与排名。"""
    account_ids = {row.account_id for row in rows}
    if not account_ids:
        return []
    accounts = {
        account.id: account
        for account in db.scalars(select(UpstreamAccount).where(UpstreamAccount.id.in_(account_ids))).all()
    }
    catalogs: dict[int, set[str]] = {}
    valid: list[Any] = []
    for row in rows:
        account = accounts.get(row.account_id)
        if account is None:
            continue
        if row.account_id not in catalogs:
            catalogs[row.account_id] = {record.id for record in parse_model_records(account.models_json)}
        if row.model in catalogs[row.account_id]:
            valid.append(row)
    return valid


def _leaderboard_item(index: int, row: Any) -> dict[str, Any]:
    return {
        "rank": index + 1,
        "account_id": row.account_id,
        "account_name": row.account_name,
        "provider": row.provider,
        "model": row.model,
        "avg_speed": round(float(row.avg_speed), 2) if row.avg_speed is not None else None,
        "best_speed": round(float(row.best_speed), 2) if row.best_speed is not None else None,
        "samples": int(row.samples or 0),
        "avg_first_token_ms": round(float(row.avg_first_token_ms), 1) if row.avg_first_token_ms is not None else None,
        "avg_total_ms": round(float(row.avg_total_ms), 1) if row.avg_total_ms is not None else None,
        "last_tested_at": row.last_tested_at.isoformat() if row.last_tested_at is not None else None,
    }


@router.get("/leaderboard")
def benchmark_leaderboard(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: Admin = Depends(get_current_admin),
) -> dict[str, Any]:
    """按「账号 × 模型」聚合已保存测速结果的输出速度，按平均速度排名。"""
    rows = _valid_leaderboard_rows(db, _leaderboard_rows(db, max(limit, 500)))[:limit]
    return {"items": [_leaderboard_item(index, row) for index, row in enumerate(rows)]}


@router.get("/leaderboard/export")
def export_benchmark_leaderboard(
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    rows = _valid_leaderboard_rows(db, _leaderboard_rows(db, max(limit, 500)))[:limit]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["排名", "模型", "账号", "供应商", "平均速度(tok/s)", "最佳速度(tok/s)", "样本", "平均首token(ms)", "平均总耗时(ms)", "最近测试"])
    for index, row in enumerate(rows):
        item = _leaderboard_item(index, row)
        writer.writerow([
            item["rank"], item["model"], item["account_name"], item["provider"],
            item["avg_speed"], item["best_speed"], item["samples"],
            item["avg_first_token_ms"], item["avg_total_ms"], item["last_tested_at"] or "",
        ])
    content = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="benchmark-leaderboard-{datetime.now().strftime("%Y%m%d-%H%M%S")}.csv"'},
    )
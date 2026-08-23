from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.clock import utcnow
from app.db import get_db
from app.deps import get_current_admin
from app.models import Admin, BenchmarkResult, BenchmarkRun

router = APIRouter(
    prefix="/api/admin/benchmark/history",
    tags=["admin-benchmark-history"],
    dependencies=[Depends(get_current_admin)],
)


class BenchmarkResultInput(BaseModel):
    account_id: int = Field(ge=1)
    account_name: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=32)
    model: str = Field(min_length=1, max_length=128)
    ok: bool
    timeout: bool = False
    first_token_ms: float | None = Field(default=None, ge=0, le=3_600_000)
    total_ms: float | None = Field(default=None, ge=0, le=3_600_000)
    output_chars: int | None = Field(default=None, ge=0, le=10_000_000)
    estimated_output_tokens: int | None = Field(default=None, ge=0, le=2_500_000)
    output_tokens_per_second: float | None = Field(default=None, ge=0, le=1_000_000)
    preview: str | None = Field(default=None, max_length=500)
    error: str | None = Field(default=None, max_length=500)


class SaveBenchmarkRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    max_tokens: int = Field(ge=1, le=512)
    results: list[BenchmarkResultInput] = Field(min_length=1, max_length=1000)


def result_to_dict(result: BenchmarkResult) -> dict[str, Any]:
    return {
        "id": result.id,
        "account_id": result.account_id,
        "account_name": result.account_name,
        "provider": result.provider,
        "model": result.model,
        "ok": result.ok,
        "timeout": result.timeout,
        "first_token_ms": result.first_token_ms,
        "total_ms": result.total_ms,
        "output_chars": result.output_chars,
        "estimated_output_tokens": result.estimated_output_tokens,
        "output_tokens_per_second": result.output_tokens_per_second,
        "preview": result.preview,
        "error": result.error,
    }


def run_to_dict(run: BenchmarkRun, include_results: bool = True) -> dict[str, Any]:
    data = {
        "id": run.id,
        "prompt": run.prompt,
        "max_tokens": run.max_tokens,
        "created_at": run.created_at.isoformat(),
        "result_count": len(run.results),
        "success_count": sum(1 for result in run.results if result.ok),
    }
    if include_results:
        data["results"] = [result_to_dict(result) for result in run.results]
    return data


@router.post("")
def save_benchmark(
    payload: SaveBenchmarkRequest,
    db: Session = Depends(get_db),
    _: Admin = Depends(get_current_admin),
) -> dict[str, Any]:
    run = BenchmarkRun(prompt=payload.prompt, max_tokens=payload.max_tokens, created_at=utcnow())
    for item in payload.results:
        run.results.append(
            BenchmarkResult(
                account_id=item.account_id,
                account_name=item.account_name,
                provider=item.provider,
                model=item.model,
                ok=item.ok,
                timeout=item.timeout,
                first_token_ms=item.first_token_ms,
                total_ms=item.total_ms,
                output_chars=item.output_chars,
                estimated_output_tokens=item.estimated_output_tokens,
                output_tokens_per_second=item.output_tokens_per_second,
                preview=item.preview,
                error=item.error,
            )
        )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run_to_dict(run)


@router.get("")
def list_benchmark_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    total = db.scalar(select(func.count()).select_from(BenchmarkRun)) or 0
    rows = db.scalars(
        select(BenchmarkRun)
        .options(selectinload(BenchmarkRun.results))
        .order_by(BenchmarkRun.created_at.desc(), BenchmarkRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"items": [run_to_dict(row, include_results=False) for row in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/export")
def export_benchmark_runs(db: Session = Depends(get_db)) -> StreamingResponse:
    rows = db.execute(
        select(BenchmarkResult, BenchmarkRun.created_at)
        .join(BenchmarkRun, BenchmarkResult.run_id == BenchmarkRun.id)
        .order_by(BenchmarkRun.created_at.desc(), BenchmarkResult.id.asc())
    ).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["测试时间", "会话 ID", "账号", "供应商", "模型", "状态", "首 token(ms)", "总耗时(ms)", "输出速度(tok/s)", "响应预览", "错误"])
    for result, created_at in rows:
        writer.writerow([
            created_at.isoformat(), result.run_id, result.account_name, result.provider, result.model,
            "超时" if result.timeout else "成功" if result.ok else "失败", result.first_token_ms, result.total_ms,
            result.output_tokens_per_second, result.preview or "", result.error or "",
        ])
    content = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="benchmark-history-{datetime.now().strftime("%Y%m%d-%H%M%S")}.csv"'},
    )


@router.get("/{run_id}")
def get_benchmark_run(run_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    run = db.scalar(select(BenchmarkRun).options(selectinload(BenchmarkRun.results)).where(BenchmarkRun.id == run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="测速记录不存在")
    return run_to_dict(run)

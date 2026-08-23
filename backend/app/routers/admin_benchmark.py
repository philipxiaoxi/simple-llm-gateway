from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.deps import get_current_admin
from app.models import Admin, UpstreamAccount
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
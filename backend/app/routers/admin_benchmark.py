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
from app.services.credentials import CredentialError, require_upstream_credential

router = APIRouter(prefix="/api/admin/benchmark", tags=["admin-benchmark"], dependencies=[Depends(get_current_admin)])

FIRST_TOKEN_TIMEOUT_SECONDS = 60


class BenchmarkRequest(BaseModel):
    account_id: int
    model: str = Field(min_length=1, max_length=200)
    prompt: str = Field(default="用一句话介绍你自己。", min_length=1, max_length=2000)
    max_tokens: int = Field(default=64, ge=1, le=512)


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
        response = await provider.complete(
            account,
            [{"role": "user", "content": payload.prompt}],
            payload.model,
            True,
            {"max_tokens": payload.max_tokens},
            token,
        )
        first_token_ms: float | None = None
        output_text = ""
        response_iterator = response.__aiter__()
        try:
            first_chunk = await asyncio.wait_for(
                response_iterator.__anext__(), timeout=FIRST_TOKEN_TIMEOUT_SECONDS
            )
        except (TimeoutError, StopAsyncIteration):
            return {
                "ok": False,
                "timeout": True,
                "account_id": account.id,
                "account_name": account.name,
                "provider": account.provider,
                "model": payload.model,
                "error": "首 token 等待超过 60 秒",
            }

        for chunk in [first_chunk]:
            if first_token_ms is None:
                first_token_ms = (time.perf_counter() - started) * 1000
            choices = getattr(chunk, "choices", None) or []
            for choice in choices:
                delta = getattr(choice, "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if content:
                    output_text += str(content)
        async for chunk in response_iterator:
            choices = getattr(chunk, "choices", None) or []
            for choice in choices:
                delta = getattr(choice, "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if content:
                    output_text += str(content)
        total_ms = (time.perf_counter() - started) * 1000
        output_chars = len(output_text)
        estimated_tokens = max(0, round(output_chars / 4))
        return {
            "ok": True,
            "account_id": account.id,
            "account_name": account.name,
            "provider": account.provider,
            "model": payload.model,
            "first_token_ms": round(first_token_ms or total_ms, 1),
            "total_ms": round(total_ms, 1),
            "output_chars": output_chars,
            "estimated_output_tokens": estimated_tokens,
            "output_tokens_per_second": round(estimated_tokens / max(total_ms / 1000, 0.001), 2),
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
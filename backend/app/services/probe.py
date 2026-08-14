from __future__ import annotations

from datetime import datetime

import httpx

from app.config import get_settings
from app.models import UpstreamAccount
from app.providers import openai_api_base
from app.services.credentials import get_upstream_credential


def _candidate_urls(account: UpstreamAccount) -> list[str]:
    base = openai_api_base(account.provider, account.base_url)
    urls = [f"{base}/models"]
    stripped = account.base_url.rstrip("/")
    if f"{stripped}/models" not in urls:
        urls.append(f"{stripped}/models")
    if f"{stripped}/v1/models" not in urls:
        urls.append(f"{stripped}/v1/models")
    # unique preserve order
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


async def probe_account(account: UpstreamAccount) -> dict:
    settings = get_settings()
    token = get_upstream_credential(account)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    last_error = "未发起请求"
    started = datetime.utcnow()
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        for url in _candidate_urls(account):
            try:
                response = await client.get(url, headers=headers)
            except httpx.HTTPError as error:
                last_error = str(error)
                continue
            latency = int((datetime.utcnow() - started).total_seconds() * 1000)
            if response.status_code < 400:
                account.last_probe_ok = True
                account.last_probe_latency_ms = latency
                account.last_probe_message = f"{response.status_code} {url}"
                account.last_probe_at = datetime.utcnow()
                return {
                    "ok": True,
                    "latency_ms": latency,
                    "message": account.last_probe_message,
                }
            last_error = f"{response.status_code} {response.text[:300]}"
    latency = int((datetime.utcnow() - started).total_seconds() * 1000)
    account.last_probe_ok = False
    account.last_probe_latency_ms = latency
    account.last_probe_message = last_error
    account.last_probe_at = datetime.utcnow()
    return {"ok": False, "latency_ms": latency, "message": last_error}

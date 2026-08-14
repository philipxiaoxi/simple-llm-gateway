from __future__ import annotations

import json
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


def _extract_model_ids(payload: object) -> list[str]:
    names: list[str] = []
    if isinstance(payload, dict):
        items = payload.get("data") or payload.get("models") or payload.get("items") or []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, str):
                    names.append(item)
                elif isinstance(item, dict):
                    model_id = item.get("id") or item.get("name") or item.get("model")
                    if model_id:
                        names.append(str(model_id))
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                model_id = item.get("id") or item.get("name") or item.get("model")
                if model_id:
                    names.append(str(model_id))
    return names


async def list_account_models(account: UpstreamAccount) -> dict:
    settings = get_settings()
    token = get_upstream_credential(account, allow_expired=True)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    last_error = "未发起请求"
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        for url in _candidate_urls(account):
            try:
                response = await client.get(url, headers=headers)
            except httpx.HTTPError as error:
                last_error = str(error)
                continue
            if response.status_code >= 400:
                last_error = f"{response.status_code} {response.text[:300]}"
                continue
            try:
                payload = response.json()
            except ValueError:
                last_error = "上游返回的不是 JSON"
                continue
            models = _extract_model_ids(payload)
            if models:
                account.models_json = json.dumps(models, ensure_ascii=False)
                account.models_updated_at = datetime.utcnow()
                return {"ok": True, "models": models, "source": url}
            last_error = f"{url} 没有解析到模型"
    return {"ok": False, "models": [], "message": last_error}

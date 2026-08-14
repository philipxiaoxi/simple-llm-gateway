from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx

from sqlalchemy.orm import object_session

from app.config import get_settings
from app.models import UpstreamAccount
from app.providers import openai_api_base
from app.services.credentials import get_upstream_credential
from app.services.grok_oauth import refresh_if_needed

GROK_WEEKLY_BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
GROK_CLI_VERSION = "0.2.114"


async def refresh_quota(account: UpstreamAccount) -> dict[str, Any]:
    token = get_upstream_credential(account, allow_expired=True)
    if not token:
        payload = {"supported": False, "message": "账号没有可用凭证"}
        account.quota_json = json.dumps(payload, ensure_ascii=False)
        account.quota_updated_at = datetime.utcnow()
        return payload

    if account.provider == "deepseek":
        payload = await _deepseek_balance(account, token)
    elif account.provider == "grok":
        payload = await _grok_quota(account, token)
    elif account.provider == "opencode_go":
        payload = await _opencode_go_quota(account, token)
    else:
        payload = await _generic_quota(account, token)

    account.quota_json = json.dumps(payload, ensure_ascii=False)
    account.quota_updated_at = datetime.utcnow()
    return payload


OPENCODE_GO_WINDOWS: tuple[tuple[str, str, float], ...] = (
    ("rolling", "5 小时限额", 12.0),
    ("weekly", "周限制", 30.0),
    ("monthly", "月限制", 60.0),
)


def parse_opencode_go_windows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        return []
    windows: list[dict[str, Any]] = []
    for window_id, label, limit_usd in OPENCODE_GO_WINDOWS:
        item = usage.get(window_id)
        if not isinstance(item, dict):
            continue
        percent = item.get("percent")
        try:
            percent_value = float(percent)
        except (TypeError, ValueError):
            continue
        windows.append(
            {
                "id": window_id,
                "label": label,
                "percent": percent_value,
                "limit_usd": limit_usd,
                "used_usd": round(limit_usd * percent_value / 100, 2),
                "resets_at": item.get("resetsAt") or item.get("resets_at"),
                "status": str(item.get("status") or "ok"),
            }
        )
    return windows


async def _opencode_go_quota(account: UpstreamAccount, token: str) -> dict[str, Any]:
    settings = get_settings()
    url = openai_api_base(account.provider, account.base_url).rstrip("/") + "/usage"
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        try:
            response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as error:
            return {"supported": True, "ok": False, "provider": "opencode_go", "message": str(error)}
    if response.status_code >= 400:
        return {
            "supported": True,
            "ok": False,
            "provider": "opencode_go",
            "message": f"{response.status_code} {response.text[:300]}",
        }
    try:
        raw = response.json()
    except ValueError:
        return {"supported": True, "ok": False, "provider": "opencode_go", "message": "上游返回的不是 JSON"}
    if not isinstance(raw, dict):
        return {"supported": True, "ok": False, "provider": "opencode_go", "message": "额度格式无法识别"}
    windows = parse_opencode_go_windows(raw)
    if not windows:
        return {
            "supported": True,
            "ok": False,
            "provider": "opencode_go",
            "raw": raw,
            "message": "没有解析到 5 小时 / 周 / 月额度",
        }
    return {
        "supported": True,
        "ok": True,
        "provider": "opencode_go",
        "windows": windows,
        "raw": raw,
    }


async def _deepseek_balance(account: UpstreamAccount, token: str) -> dict[str, Any]:
    settings = get_settings()
    url = account.base_url.rstrip("/") + "/user/balance"
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    if response.status_code >= 400:
        return {
            "supported": True,
            "ok": False,
            "message": f"{response.status_code} {response.text[:300]}",
        }
    body = response.json()
    return {"supported": True, "ok": True, "provider": "deepseek", "raw": body}


def parse_grok_weekly_windows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    config = raw.get("config")
    if not isinstance(config, dict):
        return []
    percent = config.get("creditUsagePercent")
    if percent is None:
        products = config.get("productUsage")
        if isinstance(products, list):
            for item in products:
                if isinstance(item, dict) and item.get("usagePercent") is not None:
                    percent = item.get("usagePercent")
                    break
    try:
        percent_value = float(percent)
    except (TypeError, ValueError):
        return []
    period = config.get("currentPeriod") if isinstance(config.get("currentPeriod"), dict) else {}
    resets_at = (period or {}).get("end") or config.get("billingPeriodEnd")
    return [
        {
            "id": "weekly",
            "label": "周限制",
            "percent": percent_value,
            "limit_usd": None,
            "used_usd": None,
            "resets_at": resets_at,
            "status": "ok",
        }
    ]


def _grok_billing_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-xai-token-auth": "xai-grok-cli",
        "x-grok-client-version": GROK_CLI_VERSION,
        "User-Agent": f"grok-pager/{GROK_CLI_VERSION} grok-shell/{GROK_CLI_VERSION} (macos; aarch64)",
    }


async def _grok_access_token(account: UpstreamAccount, token: str) -> str:
    session = object_session(account)
    if session is None:
        return token
    try:
        return await refresh_if_needed(session, account)
    except ValueError:
        return token


async def _grok_quota(account: UpstreamAccount, token: str) -> dict[str, Any]:
    settings = get_settings()
    access_token = await _grok_access_token(account, token)
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        try:
            response = await client.get(GROK_WEEKLY_BILLING_URL, headers=_grok_billing_headers(access_token))
        except httpx.HTTPError as error:
            return {"supported": True, "ok": False, "provider": "grok", "message": str(error)}
    if response.status_code == 401:
        return {"supported": True, "ok": False, "provider": "grok", "message": "Grok 授权失效，请重新授权"}
    if response.status_code >= 400:
        return {
            "supported": True,
            "ok": False,
            "provider": "grok",
            "message": f"{response.status_code} {response.text[:300]}",
        }
    try:
        raw = response.json()
    except ValueError:
        return {"supported": True, "ok": False, "provider": "grok", "message": "上游返回的不是 JSON"}
    if not isinstance(raw, dict):
        return {"supported": True, "ok": False, "provider": "grok", "message": "额度格式无法识别"}
    windows = parse_grok_weekly_windows(raw)
    if not windows:
        return {
            "supported": True,
            "ok": False,
            "provider": "grok",
            "raw": raw,
            "message": "没有解析到周额度",
        }
    return {
        "supported": True,
        "ok": True,
        "provider": "grok",
        "windows": windows,
        "raw": raw,
    }


async def _generic_quota(account: UpstreamAccount, token: str) -> dict[str, Any]:
    settings = get_settings()
    base = openai_api_base(account.provider, account.base_url)
    headers = {"Authorization": f"Bearer {token}"}
    candidates = [f"{base}/usage", f"{account.base_url.rstrip('/')}/usage", f"{base}/billing"]
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        for url in candidates:
            try:
                response = await client.get(url, headers=headers)
            except httpx.HTTPError:
                continue
            if response.status_code < 400:
                try:
                    raw = response.json()
                except ValueError:
                    raw = {"text": response.text[:500]}
                return {"supported": True, "ok": True, "provider": account.provider, "raw": raw}
    return {
        "supported": False,
        "ok": False,
        "message": "该供应商不支持查询余额",
        "provider": account.provider,
    }

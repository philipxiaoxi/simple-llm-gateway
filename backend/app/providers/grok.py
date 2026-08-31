from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import object_session

from app.config import get_settings
from app.crypto import decrypt_secret
from app.models import UpstreamAccount
from app.providers.base import OpenAICompatibleProvider, QuotaItem, QuotaView
from app.services.grok_oauth import refresh_if_needed


GROK_WEEKLY_BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"


def parse_grok_weekly(raw: dict[str, Any]) -> tuple[float, str | None] | None:
    config = raw.get("config")
    if not isinstance(config, dict):
        return None
    percent = config.get("creditUsagePercent")
    if percent is None:
        products = config.get("productUsage")
        if isinstance(products, list):
            for entry in products:
                if isinstance(entry, dict) and entry.get("usagePercent") is not None:
                    percent = entry.get("usagePercent")
                    break
    try:
        percent_value = float(percent)
    except (TypeError, ValueError):
        return None
    period = config.get("currentPeriod") if isinstance(config.get("currentPeriod"), dict) else {}
    resets_at = (period or {}).get("end") or config.get("billingPeriodEnd")
    return percent_value, str(resets_at) if resets_at else None


def grok_quota_items(raw: dict[str, Any]) -> list[QuotaItem]:
    parsed = parse_grok_weekly(raw)
    if parsed is None:
        return []
    percent_value, resets_at = parsed
    items = [QuotaItem(label="周限制", type="progress", value=percent_value)]
    if resets_at:
        items.append(QuotaItem(label="周限制", type="text", value=f"重置时间：{resets_at}"))
    return items


class GrokProvider(OpenAICompatibleProvider):
    id = "grok"
    label = "Grok"
    auth_type = "oauth"
    default_base_url = "https://api.x.ai/v1"
    default_models = ["grok-4", "grok-4.6", "grok-3", "grok-2"]

    def missing_credential(self, account: UpstreamAccount) -> bool:
        return account.oauth_token is None and not account.api_key_encrypted

    async def prepare_credential(self, account: UpstreamAccount, db: Any) -> str:
        if account.oauth_token is not None:
            try:
                return await refresh_if_needed(db, account)
            except ValueError:
                if account.api_key_encrypted:
                    return decrypt_secret(account.api_key_encrypted, get_settings().app_secret_key)
                raise
        if account.api_key_encrypted:
            return decrypt_secret(account.api_key_encrypted, get_settings().app_secret_key)
        raise ValueError("Grok 账号尚未授权")

    async def _access_token(self, account: UpstreamAccount, token: str) -> str:
        session = object_session(account)
        if session is None:
            return token
        try:
            return await refresh_if_needed(session, account)
        except ValueError:
            return token

    async def load_quota(self, account: UpstreamAccount, token: str) -> QuotaView:
        settings = get_settings()
        access_token = await self._access_token(account, token)
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            try:
                response = await client.get(
                    GROK_WEEKLY_BILLING_URL,
                    headers={**self.outbound_headers(account, access_token), "Accept": "application/json"},
                )
            except httpx.HTTPError as error:
                return QuotaView(ok=False, message=str(error))
        if response.status_code == 401:
            return QuotaView(ok=False, message="Grok 授权失效，请重新授权")
        if response.status_code >= 400:
            return QuotaView(ok=False, message=f"{response.status_code} {response.text[:300]}")
        try:
            raw = response.json()
        except ValueError:
            return QuotaView(ok=False, message="上游返回的不是 JSON")
        if not isinstance(raw, dict):
            return QuotaView(ok=False, message="额度格式无法识别")
        items = grok_quota_items(raw)
        if not items:
            return QuotaView(ok=False, message="没有解析到周额度")
        return QuotaView(ok=True, items=items)

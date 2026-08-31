from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.models import UpstreamAccount
from app.providers.base import Provider, QuotaItem, QuotaView


def format_money(currency: str, amount: float) -> str:
    if currency == "CNY":
        return f"¥{amount:.2f}"
    if currency == "USD":
        return f"${amount:.2f}"
    return f"{currency} {amount:.2f}"


def parse_deepseek_balances(raw: dict[str, Any]) -> list[dict[str, Any]]:
    entries = raw.get("balance_infos")
    if not isinstance(entries, list):
        return []
    balances: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        currency = str(entry.get("currency") or "").strip() or "USD"
        try:
            total = float(entry.get("total_balance"))
        except (TypeError, ValueError):
            continue

        def money_field(field_name: str) -> float:
            try:
                return float(entry.get(field_name) or 0)
            except (TypeError, ValueError):
                return 0.0

        balances.append(
            {
                "currency": currency,
                "total": total,
                "granted": money_field("granted_balance"),
                "topped_up": money_field("topped_up_balance"),
            }
        )
    return balances


def deepseek_quota_items(raw: dict[str, Any]) -> list[QuotaItem]:
    items: list[QuotaItem] = []
    for balance in parse_deepseek_balances(raw):
        currency = str(balance["currency"])
        items.append(
            QuotaItem(label=currency, type="text", value=format_money(currency, float(balance["total"])))
        )
        items.append(
            QuotaItem(
                label="构成",
                type="text",
                value=(
                    f"赠送 {format_money(currency, float(balance['granted']))} · "
                    f"充值 {format_money(currency, float(balance['topped_up']))}"
                ),
            )
        )
    if raw.get("is_available") is False:
        items.append(QuotaItem(label="状态", type="text", value="余额不足，可能无法继续调用"))
    return items


class DeepSeekProvider(Provider):
    id = "deepseek"
    label = "DeepSeek"
    auth_type = "api_key"
    default_base_url = "https://api.deepseek.com"
    default_models = ["deepseek-chat", "deepseek-reasoner"]

    async def load_quota(self, account: UpstreamAccount, token: str) -> QuotaView:
        settings = get_settings()
        url = account.base_url.rstrip("/") + "/user/balance"
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            try:
                response = await client.get(url, headers=self.outbound_headers(account, token))
            except httpx.HTTPError as error:
                return QuotaView(ok=False, message=str(error))
        if response.status_code >= 400:
            return QuotaView(ok=False, message=f"{response.status_code} {response.text[:300]}")
        try:
            body = response.json()
        except ValueError:
            return QuotaView(ok=False, message="上游返回的不是 JSON")
        if not isinstance(body, dict):
            return QuotaView(ok=False, message="余额格式无法识别")
        items = deepseek_quota_items(body)
        if not items:
            return QuotaView(ok=False, message="没有解析到余额")
        return QuotaView(ok=True, items=items)

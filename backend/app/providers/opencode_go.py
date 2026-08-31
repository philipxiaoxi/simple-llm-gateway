from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.models import UpstreamAccount
from app.providers.base import OpenAICompatibleProvider, QuotaItem, QuotaView


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
        entry = usage.get(window_id)
        if not isinstance(entry, dict):
            continue
        percent = entry.get("percent")
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
                "resets_at": entry.get("resetsAt") or entry.get("resets_at"),
                "status": str(entry.get("status") or "ok"),
            }
        )
    return windows


def opencode_go_quota_items(raw: dict[str, Any]) -> list[QuotaItem]:
    items: list[QuotaItem] = []
    for window in parse_opencode_go_windows(raw):
        label = str(window["label"])
        items.append(QuotaItem(label=label, type="progress", value=window["percent"]))
        detail = f"${window['used_usd']:.2f} / ${window['limit_usd']:.2f}"
        resets_at = window.get("resets_at")
        if resets_at:
            detail = f"{detail} · 重置时间：{resets_at}"
        status = window.get("status")
        if status and status != "ok":
            detail = f"{detail} · {status}"
        items.append(QuotaItem(label=label, type="text", value=detail))
    return items


class OpenCodeGoProvider(OpenAICompatibleProvider):
    id = "opencode_go"
    label = "OpenCode Go"
    auth_type = "api_key"
    default_base_url = "https://opencode.ai/zen/go/v1"
    default_models = ["glm-5.3", "glm-5.2", "kimi-k2.6", "kimi-k2.7-code", "minimax-m2.7"]

    async def load_quota(self, account: UpstreamAccount, token: str) -> QuotaView:
        settings = get_settings()
        url = self.openai_api_base(account.base_url).rstrip("/") + "/usage"
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            try:
                response = await client.get(url, headers=self.outbound_headers(account, token))
            except httpx.HTTPError as error:
                return QuotaView(ok=False, message=str(error))
        if response.status_code >= 400:
            return QuotaView(ok=False, message=f"{response.status_code} {response.text[:300]}")
        try:
            raw = response.json()
        except ValueError:
            return QuotaView(ok=False, message="上游返回的不是 JSON")
        if not isinstance(raw, dict):
            return QuotaView(ok=False, message="额度格式无法识别")
        items = opencode_go_quota_items(raw)
        if not items:
            return QuotaView(ok=False, message="没有解析到 5 小时 / 周 / 月额度")
        return QuotaView(ok=True, items=items)

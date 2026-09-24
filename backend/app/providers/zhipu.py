from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.models import UpstreamAccount
from app.providers.base import OpenAICompatibleProvider, QuotaItem, QuotaView, quota_error_view, quota_http_timeout

ZHIPU_DEFAULT_ORIGIN = "https://open.bigmodel.cn"
ZHIPU_QUOTA_PATH = "/api/monitor/usage/quota/limit"


def zhipu_origin(base_url: str) -> str:
    """从账号 base URL 取出站点根地址，额度接口挂在站点根上而非 /api/paas/v4。"""
    parts = urlsplit((base_url or "").strip())
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return ZHIPU_DEFAULT_ORIGIN


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _as_number(value)
        if number is not None:
            return number
    return None


def _format_amount(value: float | None) -> str:
    if value is None:
        return "0"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def _format_reset_at(value: Any) -> str | None:
    """nextResetTime 是 epoch 毫秒（少数站点给秒），转成前端可识别的 ISO 时间。"""
    epoch = _as_number(value)
    if epoch is None or epoch <= 0:
        return None
    if epoch < 1e10:
        epoch *= 1000
    try:
        moment = datetime.fromtimestamp(epoch / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.isoformat()


def zhipu_limit_label(limit: dict[str, Any]) -> str:
    type_name = str(limit.get("type") or "").strip().upper()
    unit = _as_number(limit.get("unit"))
    number = _as_number(limit.get("number"))
    name = limit.get("name")
    named = name.strip() if isinstance(name, str) and name.strip() else ""
    if type_name == "TOKENS_LIMIT":
        if unit == 3:
            hours = int(number) if number and number > 0 else 5
            return f"{hours} 小时限额"
        if unit == 6:
            return "周限制"
        return named or "Token 额度"
    if type_name == "TIME_LIMIT":
        return named or "工具额度"
    if type_name == "CREDIT_LIMIT":
        return named or "积分额度"
    if named:
        return named
    if type_name:
        return type_name.replace("_LIMIT", "").replace("_", " ")
    return "额度"


def zhipu_quota_items(raw: dict[str, Any]) -> list[QuotaItem]:
    """解析 /api/monitor/usage/quota/limit 的 data.limits[]。

    按量计费账号与 GLM Coding Plan 共用这个接口，返回的 limits[] 类型不同：
    - 按量计费 / 资源包：给 remaining / number（或 percentage），展示为剩余/总数；
    - Coding Plan：TOKENS_LIMIT unit=3 是 5 小时窗口、unit=6 是周窗口，TIME_LIMIT 是工具额度。
    """
    data = raw.get("data")
    if not isinstance(data, dict):
        return []
    limits = data.get("limits")
    if not isinstance(limits, list):
        return []
    items: list[QuotaItem] = []
    for limit in limits:
        if not isinstance(limit, dict):
            continue
        label = zhipu_limit_label(limit)
        percentage = _as_number(limit.get("percentage"))
        remaining = _first_number(limit.get("remaining"))
        total = _first_number(limit.get("number"), limit.get("total"), limit.get("quantity"))
        if percentage is None and remaining is not None and total:
            percentage = round((total - remaining) / total * 100, 1)
        if percentage is None:
            continue
        items.append(QuotaItem(label=label, type="progress", value=percentage))
        details: list[str] = []
        if remaining is not None and total is not None:
            details.append(f"剩余 {_format_amount(remaining)} / {_format_amount(total)}")
        reset_at = _format_reset_at(limit.get("nextResetTime"))
        if reset_at:
            details.append(f"重置时间：{reset_at}")
        if details:
            items.append(QuotaItem(label=label, type="text", value=" · ".join(details)))
    level = data.get("level")
    if isinstance(level, str) and level.strip():
        items.append(QuotaItem(label="套餐", type="text", value=level.strip()))
    return items


class ZhipuProvider(OpenAICompatibleProvider):
    id = "zhipu"
    label = "智谱"
    auth_type = "api_key"
    default_base_url = "https://open.bigmodel.cn/api/paas/v4"
    default_models = ["glm-5.3", "glm-4.7", "glm-4.6", "glm-4.5", "glm-4.5-air"]

    async def load_quota(self, account: UpstreamAccount, token: str) -> QuotaView:
        url = zhipu_origin(account.base_url) + ZHIPU_QUOTA_PATH
        headers = {
            **self.outbound_headers(account, token),
            "Accept": "application/json",
            "Accept-Language": "en-US,en",
        }
        async with httpx.AsyncClient(timeout=quota_http_timeout()) as client:
            try:
                response = await client.get(url, headers=headers)
            except httpx.HTTPError as error:
                return quota_error_view(error)
        if response.status_code in (401, 403):
            return QuotaView(ok=False, message="智谱凭证无效或无权查询额度")
        if response.status_code >= 400:
            return QuotaView(ok=False, message=f"{response.status_code} {response.text[:300]}")
        try:
            raw = response.json()
        except ValueError:
            return QuotaView(ok=False, message="上游返回的不是 JSON")
        if not isinstance(raw, dict):
            return QuotaView(ok=False, message="额度格式无法识别")
        if raw.get("success") is False or not isinstance(raw.get("data"), dict):
            reason = raw.get("msg") or raw.get("message") or "上游未返回额度"
            return QuotaView(ok=False, message=str(reason)[:300])
        items = zhipu_quota_items(raw)
        if not items:
            return QuotaView(ok=False, message="没有解析到额度信息")
        return QuotaView(ok=True, items=items)

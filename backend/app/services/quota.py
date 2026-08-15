from __future__ import annotations

from typing import Any

from app.models import UpstreamAccount
from app.providers import get_provider


async def refresh_quota(account: UpstreamAccount) -> dict[str, Any]:
    return await get_provider(account.provider).fetch_quota(account)

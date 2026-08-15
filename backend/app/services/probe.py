from __future__ import annotations

from app.models import UpstreamAccount
from app.providers import get_provider


async def probe_account(account: UpstreamAccount) -> dict:
    return await get_provider(account.provider).probe(account)


async def list_account_models(account: UpstreamAccount) -> dict:
    return await get_provider(account.provider).list_models(account)

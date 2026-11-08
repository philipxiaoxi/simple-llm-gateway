from __future__ import annotations

from app.models import UpstreamAccount
from app.providers.base import GENERIC_QUOTA_UNSUPPORTED, OpenAICompatibleProvider, QuotaView


class OpenAIGenericProvider(OpenAICompatibleProvider):
    id = "openai_generic"
    label = "通用 OpenAI"
    auth_type = "api_key"
    default_base_url = "https://api.openai.com/v1"
    default_models: list[str] = []

    def supports_quota(self) -> bool:
        return False

    def initial_quota(self) -> QuotaView | None:
        return None

    async def load_quota(self, account: UpstreamAccount, token: str) -> QuotaView:
        return QuotaView(ok=False, message=GENERIC_QUOTA_UNSUPPORTED)

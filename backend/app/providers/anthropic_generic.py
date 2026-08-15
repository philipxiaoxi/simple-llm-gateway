from __future__ import annotations

from typing import Any

import httpx
import litellm

from app.config import get_settings
from app.models import UpstreamAccount
from app.providers.base import GENERIC_QUOTA_UNSUPPORTED, Provider, QuotaView
from app.services.credentials import get_upstream_credential

ANTHROPIC_VERSION = "2023-06-01"


def _json_or_error(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {
            "type": "error",
            "error": {"type": "api_error", "message": response.text[:300]},
        }


class AnthropicGenericProvider(Provider):
    id = "anthropic_generic"
    label = "通用 Anthropic"
    auth_type = "api_key"
    default_base_url = "https://api.anthropic.com"
    default_models: list[str] = []
    upstream_protocol = "anthropic"

    def auth_headers(self, token: str) -> dict[str, str]:
        headers = {"anthropic-version": ANTHROPIC_VERSION}
        if token:
            headers["x-api-key"] = token
        return headers

    def can_passthrough(self, inbound_protocol: str) -> bool:
        return inbound_protocol == "anthropic_messages"

    def merge_inbound_headers(self, token: str, inbound_headers: dict[str, str] | None) -> dict[str, str]:
        headers = {**self.auth_headers(token), "content-type": "application/json"}
        if not inbound_headers:
            return headers
        lowered = {key.lower(): value for key, value in inbound_headers.items()}
        if lowered.get("anthropic-beta"):
            headers["anthropic-beta"] = lowered["anthropic-beta"]
        if lowered.get("anthropic-version"):
            headers["anthropic-version"] = lowered["anthropic-version"]
        return headers

    def _v1_root(self, base_url: str) -> str:
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            return base
        return f"{base}/v1"

    def _origin(self, base_url: str) -> str:
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            return base[: -len("/v1")].rstrip("/")
        return base

    def messages_url(self, account: UpstreamAccount) -> str:
        return f"{self._v1_root(account.base_url)}/messages"

    def count_tokens_url(self, account: UpstreamAccount) -> str:
        return f"{self._v1_root(account.base_url)}/messages/count_tokens"

    def model_candidate_urls(self, account: UpstreamAccount) -> list[str]:
        return [f"{self._v1_root(account.base_url)}/models"]

    def native_request(
        self,
        account: UpstreamAccount,
        token: str,
        inbound_headers: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, str]]:
        return self.messages_url(account), self.merge_inbound_headers(token, inbound_headers)

    async def complete(
        self,
        account: UpstreamAccount,
        messages: list[dict[str, Any]],
        model: str,
        stream: bool,
        extra: dict[str, Any],
        api_key: str,
    ) -> Any:
        settings = get_settings()
        return await litellm.acompletion(
            model=f"anthropic/{model}",
            messages=messages,
            api_key=api_key,
            api_base=self._origin(account.base_url),
            stream=stream,
            timeout=settings.request_timeout_seconds,
            drop_params=True,
            extra_headers=self.auth_headers(api_key),
            **extra,
        )

    async def post_native(
        self,
        account: UpstreamAccount,
        body: dict[str, Any],
        token: str,
        inbound_headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        url, headers = self.native_request(account, token, inbound_headers)
        settings = get_settings()
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=body)
        return response.status_code, _json_or_error(response)

    async def count_tokens_native(
        self,
        account: UpstreamAccount,
        body: dict[str, Any],
        inbound_headers: dict[str, str] | None = None,
    ) -> tuple[int, Any] | None:
        token = get_upstream_credential(account, allow_expired=True)
        if not token:
            return 403, {
                "type": "error",
                "error": {"type": "permission_error", "message": "上游账号尚未配置密钥或授权"},
            }
        url = self.count_tokens_url(account)
        headers = self.merge_inbound_headers(token, inbound_headers)
        settings = get_settings()
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=body)
        return response.status_code, _json_or_error(response)

    def initial_quota(self) -> QuotaView | None:
        return QuotaView(ok=False, message=GENERIC_QUOTA_UNSUPPORTED)

    async def load_quota(self, account: UpstreamAccount, token: str) -> QuotaView:
        return QuotaView(ok=False, message=GENERIC_QUOTA_UNSUPPORTED)

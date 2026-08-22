from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import litellm

from app.clock import utcnow
from app.config import get_settings
from app.models import UpstreamAccount
from app.services.credentials import get_upstream_credential, require_upstream_credential

GENERIC_QUOTA_UNSUPPORTED = "通用供应商不支持查询余额"


@dataclass
class QuotaItem:
    label: str
    type: str
    value: str | int | float


@dataclass
class QuotaView:
    ok: bool
    items: list[QuotaItem] = field(default_factory=list)
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "items": [
                {"label": item.label, "type": item.type, "value": item.value} for item in self.items
            ],
        }
        if self.message:
            payload["message"] = self.message
        return payload


def extract_model_ids(payload: object) -> list[str]:
    names: list[str] = []
    if isinstance(payload, dict):
        entries = payload.get("data") or payload.get("models") or payload.get("items") or []
        if isinstance(entries, list):
            _collect_model_ids(entries, names)
    elif isinstance(payload, list):
        _collect_model_ids(payload, names)
    return names


def _collect_model_ids(entries: list[Any], names: list[str]) -> None:
    for entry in entries:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, dict):
            model_id = entry.get("id") or entry.get("name") or entry.get("model")
            if model_id:
                names.append(str(model_id))


class Provider:
    id: str
    label: str
    auth_type: str
    default_base_url: str
    default_models: list[str] = []
    upstream_protocol: str = "openai"

    def openai_api_base(self, base_url: str) -> str:
        # 不主动补 /v1：有的上游 API 版本不是 v1（如 /v4），按用户填写的 base URL 原样使用
        return base_url.rstrip("/")

    def missing_credential(self, account: UpstreamAccount) -> bool:
        return False

    def auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"} if token else {}

    def relay_headers(self, account: UpstreamAccount) -> dict[str, str]:
        if account.source != "agent":
            return {}
        return {"X-Local-Agent-Token": get_settings().local_agent_token}

    def can_passthrough(self, inbound_protocol: str) -> bool:
        return False

    def initial_quota(self) -> QuotaView | None:
        return None

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
            model=f"openai/{model}",
            messages=messages,
            api_key=api_key,
            api_base=self.openai_api_base(account.base_url),
            stream=stream,
            timeout=settings.request_timeout_seconds,
            drop_params=True,
            extra_headers=self.relay_headers(account),
            **extra,
        )

    async def responses(
        self,
        account: UpstreamAccount,
        input_items: Any,
        model: str,
        stream: bool,
        extra: dict[str, Any],
        api_key: str,
    ) -> Any:
        settings = get_settings()
        return await litellm.aresponses(
            model=f"openai/{model}",
            input=input_items,
            api_key=api_key,
            api_base=self.openai_api_base(account.base_url),
            stream=stream,
            timeout=settings.request_timeout_seconds,
            drop_params=True,
            extra_headers=self.relay_headers(account),
            **extra,
        )

    async def post_native(
        self,
        account: UpstreamAccount,
        body: dict[str, Any],
        token: str,
        inbound_headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        raise NotImplementedError

    def native_request(
        self,
        account: UpstreamAccount,
        token: str,
        inbound_headers: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, str]]:
        raise NotImplementedError

    async def count_tokens_native(
        self,
        account: UpstreamAccount,
        body: dict[str, Any],
        inbound_headers: dict[str, str] | None = None,
    ) -> tuple[int, Any] | None:
        return None

    async def prepare_credential(self, account: UpstreamAccount, db: Any) -> str:
        if account.source == "agent":
            return "agent-managed"
        return require_upstream_credential(account)

    def model_candidate_urls(self, account: UpstreamAccount) -> list[str]:
        base = self.openai_api_base(account.base_url)
        urls = [f"{base}/models"]
        stripped = account.base_url.rstrip("/")
        for candidate in (f"{stripped}/models", f"{stripped}/v1/models"):
            if candidate not in urls:
                urls.append(candidate)
        return urls

    async def probe(self, account: UpstreamAccount) -> dict[str, Any]:
        token = "agent-managed" if account.source == "agent" else get_upstream_credential(account)
        headers = {**self.auth_headers(token or ""), **self.relay_headers(account)}
        last_error = "未发起请求"
        started = utcnow()
        settings = get_settings()
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            for url in self.model_candidate_urls(account):
                try:
                    response = await client.get(url, headers=headers)
                except httpx.HTTPError as error:
                    last_error = str(error)
                    continue
                latency = int((utcnow() - started).total_seconds() * 1000)
                if response.status_code < 400:
                    account.last_probe_ok = True
                    account.last_probe_latency_ms = latency
                    account.last_probe_message = f"{response.status_code} {url}"
                    account.last_probe_at = utcnow()
                    return {
                        "ok": True,
                        "latency_ms": latency,
                        "message": account.last_probe_message,
                    }
                last_error = f"{response.status_code} {response.text[:300]}"
        latency = int((utcnow() - started).total_seconds() * 1000)
        account.last_probe_ok = False
        account.last_probe_latency_ms = latency
        account.last_probe_message = last_error
        account.last_probe_at = utcnow()
        return {"ok": False, "latency_ms": latency, "message": last_error}

    async def list_models(self, account: UpstreamAccount) -> dict[str, Any]:
        token = "agent-managed" if account.source == "agent" else get_upstream_credential(account, allow_expired=True)
        headers = {**self.auth_headers(token or ""), **self.relay_headers(account)}
        last_error = "未发起请求"
        settings = get_settings()
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            for url in self.model_candidate_urls(account):
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
                models = extract_model_ids(payload)
                if models:
                    account.models_json = json.dumps(models, ensure_ascii=False)
                    account.models_updated_at = utcnow()
                    return {"ok": True, "models": models, "source": url}
                last_error = f"{url} 没有解析到模型"
        return {"ok": False, "models": [], "message": last_error}

    def store_quota(self, account: UpstreamAccount, view: QuotaView) -> dict[str, Any]:
        payload = view.to_dict()
        account.quota_json = json.dumps(payload, ensure_ascii=False)
        account.quota_updated_at = utcnow()
        return payload

    async def fetch_quota(self, account: UpstreamAccount) -> dict[str, Any]:
        token = get_upstream_credential(account, allow_expired=True)
        if not token:
            return self.store_quota(account, QuotaView(ok=False, message="账号没有可用凭证"))
        view = await self.load_quota(account, token)
        return self.store_quota(account, view)

    async def load_quota(self, account: UpstreamAccount, token: str) -> QuotaView:
        base = self.openai_api_base(account.base_url)
        headers = {"Authorization": f"Bearer {token}"}
        candidates = [f"{base}/usage", f"{base}/billing"]
        settings = get_settings()
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            for url in candidates:
                try:
                    response = await client.get(url, headers=headers)
                except httpx.HTTPError:
                    continue
                if response.status_code < 400:
                    try:
                        raw = response.json()
                        text = json.dumps(raw, ensure_ascii=False)
                    except ValueError:
                        text = response.text[:500]
                    return QuotaView(ok=True, items=[QuotaItem(label="额度", type="text", value=text)])
        return QuotaView(ok=False, message="该供应商不支持查询余额")


class OpenAICompatibleProvider(Provider):
    """OpenAI 兼容供应商标记基类。base URL 按用户填写原样使用，不自动补 /v1。"""

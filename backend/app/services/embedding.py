from __future__ import annotations

import hashlib
import math
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import KnowledgeBase, UpstreamAccount
from app.services.credentials import CredentialError, require_upstream_credential

ProgressCallback = Callable[[int, int, int, int], Any | Awaitable[Any]]


class EmbeddingError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class EmbeddingClient(Protocol):
    async def embed(
        self,
        texts: list[str],
        *,
        on_batch: ProgressCallback | None = None,
    ) -> list[list[float]]: ...


class FakeEmbeddingClient:
    def __init__(self, dimensions: int = 32) -> None:
        self.dimensions = max(8, dimensions)

    async def embed(
        self,
        texts: list[str],
        *,
        on_batch: ProgressCallback | None = None,
    ) -> list[list[float]]:
        results = [self._one(text) for text in texts]
        if on_batch is not None:
            await _maybe_await(on_batch(len(texts), len(texts), 1, 1))
        return results

    def _one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values = []
        while len(values) < self.dimensions:
            for byte in digest:
                values.append((byte / 255.0) * 2 - 1)
                if len(values) >= self.dimensions:
                    break
            digest = hashlib.sha256(digest).digest()
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]


class OpenAICompatibleEmbeddingClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        batch_size: int = 64,
        dimensions: int | None = None,
    ) -> None:
        self.base_url = normalize_embedding_base(base_url)
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.batch_size = max(1, batch_size)
        self.dimensions = dimensions

    async def embed(
        self,
        texts: list[str],
        *,
        on_batch: ProgressCallback | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        if not self.base_url or not self.api_key:
            raise EmbeddingError("未配置 embedding 上游地址或密钥")
        if not self.model:
            raise EmbeddingError("未指定 embedding 模型")
        payload: dict[str, Any] = {"model": self.model}
        if self.dimensions:
            payload["dimensions"] = int(self.dimensions)
        results: list[list[float]] = []
        total = len(texts)
        total_batches = max(1, (total + self.batch_size - 1) // self.batch_size)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for batch_index, start in enumerate(range(0, len(texts), self.batch_size), start=1):
                batch = texts[start : start + self.batch_size]
                request_body = dict(payload)
                request_body["input"] = batch
                try:
                    response = await client.post(
                        f"{self.base_url}/embeddings",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=request_body,
                    )
                except httpx.HTTPError as error:
                    raise EmbeddingError(f"embedding 请求失败: {error}") from error
                if response.status_code >= 400:
                    raise EmbeddingError(f"embedding 上游错误 HTTP {response.status_code}: {response.text[:300]}")
                data = response.json().get("data")
                if not isinstance(data, list) or len(data) != len(batch):
                    raise EmbeddingError("embedding 响应格式无效")
                ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
                for item in ordered:
                    vector = item.get("embedding")
                    if not isinstance(vector, list) or not vector:
                        raise EmbeddingError("embedding 向量为空")
                    results.append([float(x) for x in vector])
                if on_batch is not None:
                    await _maybe_await(on_batch(len(results), total, batch_index, total_batches))
        return results


async def _maybe_await(value):
    if hasattr(value, "__await__"):
        return await value
    return value


def normalize_embedding_base(base_url: str) -> str:
    raw = (base_url or "").strip().rstrip("/")
    if not raw:
        return ""
    if raw.endswith("/embeddings"):
        raw = raw[: -len("/embeddings")]
    return raw


def embedding_signature(model: str | None, dimensions: int | None) -> str:
    return f"{model or 'default'}|{dimensions or 'auto'}"


_override_client: EmbeddingClient | None = None


def set_embedding_client(client: EmbeddingClient | None) -> None:
    global _override_client
    _override_client = client


def get_embedding_client() -> EmbeddingClient:
    """全局默认：测试注入 > env 配置 > Fake。"""
    if _override_client is not None:
        return _override_client
    settings = get_settings()
    if settings.mcp_embedding_base_url and settings.mcp_embedding_api_key:
        return OpenAICompatibleEmbeddingClient(
            base_url=settings.mcp_embedding_base_url,
            api_key=settings.mcp_embedding_api_key,
            model=settings.mcp_embedding_model,
            timeout_seconds=float(settings.mcp_capability_timeout_seconds),
            batch_size=settings.mcp_embedding_batch_size,
        )
    return FakeEmbeddingClient(dimensions=settings.mcp_embedding_dimensions)


def _provider_api_base(account: UpstreamAccount) -> str:
    """按 provider 规则规范化 base_url（例如通用 OpenAI 需要 /v1）。"""
    try:
        from app.providers import get_provider

        return get_provider(account.provider).openai_api_base(account.base_url or "")
    except Exception:
        return (account.base_url or "").strip()


def get_embedding_client_for_base(db: Session, base: KnowledgeBase) -> EmbeddingClient:
    """知识库优先用绑定的上游账号 + 模型；否则回退全局默认。"""
    if _override_client is not None:
        return _override_client

    account_id = getattr(base, "embedding_account_id", None)
    model = (getattr(base, "embedding_model", None) or "").strip()
    dimensions = getattr(base, "embedding_dimensions", None)
    settings = get_settings()

    if account_id:
        account = db.get(UpstreamAccount, account_id)
        if account is None:
            raise EmbeddingError(f"绑定的上游账号不存在（id={account_id}）")
        if account.status != "active":
            raise EmbeddingError(f"上游账号「{account.name}」未启用")
        if not model:
            raise EmbeddingError("已绑定上游账号，请选择 embedding 模型")
        try:
            api_key = require_upstream_credential(account)
        except CredentialError as error:
            raise EmbeddingError(str(error)) from error
        base_url = _provider_api_base(account)
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise EmbeddingError(f"上游账号 base_url 无效: {base_url}")
        return OpenAICompatibleEmbeddingClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=float(settings.mcp_capability_timeout_seconds),
            batch_size=settings.mcp_embedding_batch_size,
            dimensions=dimensions,
        )

    if model:
        if settings.mcp_embedding_base_url and settings.mcp_embedding_api_key:
            return OpenAICompatibleEmbeddingClient(
                base_url=settings.mcp_embedding_base_url,
                api_key=settings.mcp_embedding_api_key,
                model=model,
                timeout_seconds=float(settings.mcp_capability_timeout_seconds),
                batch_size=settings.mcp_embedding_batch_size,
                dimensions=dimensions,
            )
        raise EmbeddingError("请选择上游账号，或配置全局 MCP_EMBEDDING_*")

    return get_embedding_client()

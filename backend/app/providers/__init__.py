from __future__ import annotations

from app.providers.anthropic_generic import AnthropicGenericProvider
from app.providers.base import Provider
from app.providers.deepseek import DeepSeekProvider
from app.providers.grok import GrokProvider
from app.providers.openai_generic import OpenAIGenericProvider
from app.providers.opencode_go import OpenCodeGoProvider

_REGISTRY: dict[str, Provider] = {}


def register_provider(provider: Provider) -> None:
    _REGISTRY[provider.id] = provider


def find_provider(provider_id: str) -> Provider | None:
    return _REGISTRY.get(provider_id)


def get_provider(provider_id: str) -> Provider:
    provider = find_provider(provider_id)
    if provider is None:
        raise ValueError(f"不支持的供应商: {provider_id}")
    return provider


def list_providers() -> list[Provider]:
    return list(_REGISTRY.values())


register_provider(OpenCodeGoProvider())
register_provider(GrokProvider())
register_provider(DeepSeekProvider())
register_provider(OpenAIGenericProvider())
register_provider(AnthropicGenericProvider())

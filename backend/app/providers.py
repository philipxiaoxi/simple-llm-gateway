from __future__ import annotations

from typing import Any


PRESETS: dict[str, dict[str, Any]] = {
    "opencode_go": {
        "auth_type": "api_key",
        "base_url": "https://opencode.ai/zen/go",
        "label": "OpenCode Go",
        "models": ["glm-5.3", "glm-5.2", "kimi-k2.6", "kimi-k2.7-code", "minimax-m2.7"],
    },
    "grok": {
        "auth_type": "oauth",
        "base_url": "https://api.x.ai/v1",
        "label": "Grok",
        "models": ["grok-4", "grok-4.6", "grok-3", "grok-2"],
    },
    "deepseek": {
        "auth_type": "api_key",
        "base_url": "https://api.deepseek.com",
        "label": "DeepSeek",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
}


def get_preset(provider: str) -> dict[str, Any]:
    if provider not in PRESETS:
        raise ValueError(f"不支持的供应商: {provider}")
    return PRESETS[provider]


def openai_api_base(provider: str, base_url: str) -> str:
    base = base_url.rstrip("/")
    if provider == "opencode_go" and not base.endswith("/v1"):
        return f"{base}/v1"
    if provider == "grok" and not base.endswith("/v1"):
        return f"{base}/v1"
    return base

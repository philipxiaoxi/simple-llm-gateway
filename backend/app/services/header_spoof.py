from __future__ import annotations

import platform
import secrets
from uuid import uuid4

HEADER_SPOOF_NONE = "none"
HEADER_SPOOF_GROK = "grok"
HEADER_SPOOF_OPENCODE = "opencode"
HEADER_SPOOF_VALUES = (HEADER_SPOOF_NONE, HEADER_SPOOF_GROK, HEADER_SPOOF_OPENCODE)

GROK_CLI_VERSION = "1.0.13"
OPENCODE_VERSION = "1.18.18"
OPENCODE_USER_AGENT = (
    f"opencode/{OPENCODE_VERSION} ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14"
)


def normalize_header_spoof(value: str | None) -> str:
    text = (value or HEADER_SPOOF_NONE).strip().lower()
    if text not in HEADER_SPOOF_VALUES:
        raise ValueError("请求头伪装只支持 none / grok / opencode")
    return text


def default_header_spoof(provider_id: str) -> str:
    if provider_id == "grok":
        return HEADER_SPOOF_GROK
    if provider_id == "opencode_go":
        return HEADER_SPOOF_OPENCODE
    return HEADER_SPOOF_NONE


def grok_runtime_tag() -> str:
    system_name = platform.system().lower()
    if system_name == "darwin":
        system_name = "macos"
    machine_name = platform.machine().lower()
    if machine_name in {"arm64", "aarch64"}:
        machine_name = "aarch64"
    elif machine_name in {"x86_64", "amd64"}:
        machine_name = "x86_64"
    return f"{system_name}; {machine_name}"


def _opencode_id(prefix: str) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return prefix + "".join(secrets.choice(alphabet) for _ in range(24))


def spoof_headers(profile: str | None, model: str | None = None) -> dict[str, str]:
    normalized = normalize_header_spoof(profile)
    if normalized == HEADER_SPOOF_GROK:
        session_id = str(uuid4())
        request_id = str(uuid4())
        headers = {
            "User-Agent": f"grok-shell/{GROK_CLI_VERSION} ({grok_runtime_tag()})",
            "x-xai-token-auth": "xai-grok-cli",
            "x-authenticateresponse": "authenticate-response",
            "x-grok-client-identifier": "grok-shell",
            "x-grok-client-version": GROK_CLI_VERSION,
            "x-grok-client-mode": "headless",
            "x-grok-session-id": session_id,
            "x-grok-conv-id": session_id,
            "x-grok-req-id": request_id,
            "x-grok-agent-id": str(uuid4()),
        }
        if model:
            headers["x-grok-model-override"] = model
        return headers
    if normalized == HEADER_SPOOF_OPENCODE:
        return {
            "User-Agent": OPENCODE_USER_AGENT,
            "x-opencode-client": "cli",
            "x-opencode-project": "global",
            "x-opencode-session": _opencode_id("ses_"),
            "x-opencode-request": _opencode_id("msg_"),
        }
    return {}

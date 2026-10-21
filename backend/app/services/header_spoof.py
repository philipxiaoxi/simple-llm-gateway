from __future__ import annotations

import hashlib
import platform
import re
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import NAMESPACE_URL, uuid4, uuid5

HEADER_SPOOF_NONE = "none"
HEADER_SPOOF_GROK = "grok"
HEADER_SPOOF_OPENCODE = "opencode"
HEADER_SPOOF_CLAUDE_CODE = "claude_code"
HEADER_SPOOF_CODEX = "codex"
HEADER_SPOOF_VALUES = (
    HEADER_SPOOF_NONE,
    HEADER_SPOOF_GROK,
    HEADER_SPOOF_OPENCODE,
    HEADER_SPOOF_CLAUDE_CODE,
    HEADER_SPOOF_CODEX,
)

GROK_CLI_VERSION = "1.0.13"
OPENCODE_VERSION = "1.18.18"
OPENCODE_USER_AGENT = (
    f"opencode/{OPENCODE_VERSION} ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14"
)
# Claude Code：官方 CLI 的 user-agent 与 x-app 组合，会话头为 X-Claude-Code-Session-Id
CLAUDE_CODE_VERSION = "2.1.280"
CLAUDE_CODE_USER_AGENT = f"claude-cli/{CLAUDE_CODE_VERSION} (external, cli)"
CLAUDE_CODE_BETA = (
    "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14,"
    "prompt-caching-scope-2026-01-05"
)
# Codex CLI：originator + session-id / thread-id 头对
CODEX_VERSION = "0.20.0"
CODEX_ORIGINATOR = "codex_cli_rs"
CODEX_USER_AGENT = f"{CODEX_ORIGINATOR}/{CODEX_VERSION}"

# OpenCode Go 要求每个请求带稳定的 x-opencode-session（用于路由与缓存亲和），
# 官方自用的 id 形如 ses_ + 64 位小写十六进制。
_OPENCODE_SESSION_RE = re.compile(r"^ses_[0-9a-f]{64}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# 一次请求内可用的伪装身份：
# - session_key：客户端自带的会话标识（优先，稳定且可与上游语义对齐）
# - stable_seed：缺会话标识时的兜底种子（api key + 账号 + 对话指纹），跨请求稳定
_SESSION_KEY: ContextVar[str | None] = ContextVar("spoof_session_key", default=None)
_STABLE_SEED: ContextVar[str | None] = ContextVar("spoof_stable_seed", default=None)


@contextmanager
def spoof_identity(session_key: str | None = None, stable_seed: str | None = None):
    """在上下文中登记本次请求的会话标识，供 spoof_headers 生成稳定伪装头。"""
    session_token = _SESSION_KEY.set(session_key or None)
    seed_token = _STABLE_SEED.set(stable_seed or None)
    try:
        yield
    finally:
        _SESSION_KEY.reset(session_token)
        _STABLE_SEED.reset(seed_token)


def normalize_header_spoof(value: str | None) -> str:
    text = (value or HEADER_SPOOF_NONE).strip().lower()
    if text not in HEADER_SPOOF_VALUES:
        raise ValueError("请求头伪装只支持 none / grok / opencode / claude_code / codex")
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


def _seed(account_seed: str | None = None) -> str | None:
    # 会话键优先（同会话稳定），其次请求级兜底种子，最后账户级稳定种子
    return _SESSION_KEY.get() or _STABLE_SEED.get() or account_seed


def _stable_hex_id(prefix: str, seed: str) -> str:
    return prefix + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _stable_uuid(tag: str, seed: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"gateway-spoof:{tag}:{seed}"))


def _stable_session_uuid(tag: str, account_seed: str | None = None) -> str:
    """会话 id：客户端给的是 UUID 就原样透传，否则由会话键确定性派生。"""
    session = _SESSION_KEY.get()
    if session and _UUID_RE.match(session):
        return session
    effective = _seed(account_seed)
    return _stable_uuid(tag, effective) if effective else str(uuid4())


def opencode_session_id(account_seed: str | None = None) -> str:
    """稳定会话 id：客户端已给出合法 id 就原样沿用，否则由会话键确定性派生。"""
    session = _SESSION_KEY.get()
    if session and _OPENCODE_SESSION_RE.match(session):
        return session
    seed = _seed(account_seed)
    if seed:
        return _stable_hex_id("ses_", f"opencode-session:{seed}")
    # 没有任何可用的会话线索时才回退随机（正常请求不应走到这里）
    return _opencode_id("ses_")


def spoof_headers(profile: str | None, model: str | None = None, seed: str | None = None) -> dict[str, str]:
    normalized = normalize_header_spoof(profile)
    if normalized == HEADER_SPOOF_GROK:
        # 客户端自带 UUID 形式的会话 id 时直接透传；否则由会话键确定性派生
        session_id = _stable_session_uuid("grok-session", seed)
        agent_seed = _STABLE_SEED.get() or seed or _SESSION_KEY.get()
        agent_id = _stable_uuid("agent", agent_seed) if agent_seed else str(uuid4())
        headers = {
            "User-Agent": f"grok-shell/{GROK_CLI_VERSION} ({grok_runtime_tag()})",
            "x-xai-token-auth": "xai-grok-cli",
            "x-authenticateresponse": "authenticate-response",
            "x-grok-client-identifier": "grok-shell",
            "x-grok-client-version": GROK_CLI_VERSION,
            "x-grok-client-mode": "headless",
            "x-grok-session-id": session_id,
            "x-grok-conv-id": session_id,
            # 请求 id 每请求唯一才是正确语义
            "x-grok-req-id": str(uuid4()),
            "x-grok-agent-id": agent_id,
        }
        if model:
            headers["x-grok-model-override"] = model
        return headers
    if normalized == HEADER_SPOOF_OPENCODE:
        return {
            "User-Agent": OPENCODE_USER_AGENT,
            "x-opencode-client": "cli",
            "x-opencode-project": "global",
            "x-opencode-session": opencode_session_id(seed),
            # 消息 id 本就每请求唯一
            "x-opencode-request": _opencode_id("msg_"),
        }
    if normalized == HEADER_SPOOF_CLAUDE_CODE:
        return {
            "User-Agent": CLAUDE_CODE_USER_AGENT,
            "x-app": "cli",
            "anthropic-beta": CLAUDE_CODE_BETA,
            "X-Claude-Code-Session-Id": _stable_session_uuid("claude-code-session", seed),
        }
    if normalized == HEADER_SPOOF_CODEX:
        # Codex 的会话由 session-id + thread-id 头对承载，主线程下两者相同
        thread_id = _stable_session_uuid("codex-thread", seed)
        return {
            "User-Agent": CODEX_USER_AGENT,
            "originator": CODEX_ORIGINATOR,
            "version": CODEX_VERSION,
            "session-id": thread_id,
            "thread-id": thread_id,
            "x-client-request-id": thread_id,
        }
    return {}

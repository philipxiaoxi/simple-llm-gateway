from __future__ import annotations

import json
from typing import Any

from app.config import get_settings
from app.crypto import decrypt_secret
from app.models import ApiKey, RequestLog, UpstreamAccount
from app.schemas import AccountOut, KeyOut, LogOut
from app.services.proxy import parse_json


def account_to_out(account: UpstreamAccount, reveal: bool = False) -> AccountOut:
    settings = get_settings()
    api_key = None
    if reveal and account.api_key_encrypted:
        api_key = decrypt_secret(account.api_key_encrypted, settings.app_secret_key)
    quota: Any = None
    if account.quota_json:
        try:
            quota = json.loads(account.quota_json)
        except json.JSONDecodeError:
            quota = account.quota_json
    models: list[str] = []
    if account.models_json:
        try:
            parsed = json.loads(account.models_json)
            if isinstance(parsed, list):
                models = [str(item) for item in parsed]
        except json.JSONDecodeError:
            models = []
    has_credential = account.source == "agent" or bool(account.api_key_encrypted) or (
        account.oauth_token is not None and bool(account.oauth_token.access_token_encrypted)
    )
    return AccountOut(
        id=account.id,
        name=account.name,
        provider=account.provider,
        source=account.source,
        agent_route_id=account.agent_route_id,
        auth_type=account.auth_type,
        base_url=account.base_url,
        website_url=account.website_url,
        status=account.status,
        risk_level=account.risk_level,
        has_credential=has_credential,
        api_key=api_key,
        last_probe_ok=account.last_probe_ok,
        last_probe_latency_ms=account.last_probe_latency_ms,
        last_probe_message=account.last_probe_message,
        last_probe_at=account.last_probe_at,
        quota=quota,
        quota_updated_at=account.quota_updated_at,
        models=models,
        models_updated_at=account.models_updated_at,
        oauth_expires_at=account.oauth_token.expires_at if account.oauth_token else None,
        created_at=account.created_at,
    )


def key_to_out(
    item: ApiKey,
    reveal: bool = False,
    today_tokens: int = 0,
    total_tokens: int = 0,
) -> KeyOut:
    settings = get_settings()
    plaintext = None
    if reveal:
        plaintext = decrypt_secret(item.key_encrypted, settings.app_secret_key)
    return KeyOut(
        id=item.id,
        name=item.name,
        key_prefix=item.key_prefix,
        key=plaintext,
        account_id=item.account_id,
        account_name=item.account.name if item.account else "",
        provider=item.account.provider if item.account else "",
        account_source=item.account.source if item.account else "upstream",
        risk_level=item.account.risk_level if item.account else "low",
        status=item.status,
        created_at=item.created_at,
        last_used_at=item.last_used_at,
        today_tokens=today_tokens,
        total_tokens=total_tokens,
    )


def log_to_out(item: RequestLog, include_bodies: bool = False) -> LogOut:
    return LogOut(
        id=item.id,
        account_id=item.account_id,
        account_name=item.account_name or (item.account.name if item.account else ""),
        account_source=item.account.source if item.account else "upstream",
        api_key_id=item.api_key_id,
        api_key_name=item.api_key_name or (item.api_key.name if item.api_key else ""),
        protocol=item.protocol,
        model=item.model,
        stream=item.stream,
        status=item.status,
        http_status=item.http_status,
        error_message=item.error_message,
        prompt_tokens=item.prompt_tokens,
        completion_tokens=item.completion_tokens,
        total_tokens=item.total_tokens,
        latency_ms=item.latency_ms,
        created_at=item.created_at,
        updated_at=item.updated_at or item.created_at,
        request_body=parse_json(item.request_body) if include_bodies else None,
        response_body=parse_json(item.response_body) if include_bodies else None,
    )

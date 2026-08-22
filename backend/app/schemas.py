from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


def normalize_website_url(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("官网地址必须是有效的 HTTP(S) 地址")
    return normalized


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str


class AdminUpdateRequest(BaseModel):
    current_password: str
    username: str | None = None
    password: str | None = None


class AccountCreate(BaseModel):
    name: str
    provider: str
    base_url: str | None = None
    website_url: str | None = None
    api_key: str | None = None
    status: str = "active"
    risk_level: str = "low"

    _normalize_website_url = field_validator("website_url")(normalize_website_url)


class AccountExportRequest(BaseModel):
    password: str


class AccountImportRequest(BaseModel):
    password: str
    payload: dict


class AccountUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    website_url: str | None = None
    api_key: str | None = None
    status: str | None = None
    risk_level: str | None = None

    _normalize_website_url = field_validator("website_url")(normalize_website_url)


class OauthCallbackComplete(BaseModel):
    account_id: int | None = None
    callback_url: str | None = None
    code: str | None = None
    state: str | None = None


class AccountOut(BaseModel):
    id: int
    name: str
    provider: str
    source: str = "upstream"
    agent_route_id: str | None = None
    auth_type: str
    base_url: str
    website_url: str | None
    status: str
    risk_level: str
    has_credential: bool
    api_key: str | None = None
    last_probe_ok: bool | None
    last_probe_latency_ms: int | None
    last_probe_message: str | None
    last_probe_at: datetime | None
    quota: Any | None = None
    quota_updated_at: datetime | None
    models: list[str] = Field(default_factory=list)
    models_updated_at: datetime | None = None
    oauth_expires_at: datetime | None = None
    created_at: datetime


class CcSwitchBuildRequest(BaseModel):
    app: str
    model: str | None = None
    haiku_model: str | None = None
    sonnet_model: str | None = None
    opus_model: str | None = None


class ShareLookupRequest(BaseModel):
    api_key: str


class ShareCcSwitchRequest(CcSwitchBuildRequest):
    api_key: str


class KeyCreate(BaseModel):
    name: str
    account_id: int


class KeyUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    account_id: int | None = None


class KeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    key: str | None = None
    account_id: int
    account_name: str
    provider: str
    account_source: str
    risk_level: str
    status: str
    created_at: datetime
    last_used_at: datetime | None
    today_tokens: int = 0
    total_tokens: int = 0


class LogOut(BaseModel):
    id: int
    account_id: int
    account_name: str = ""
    account_source: str = "upstream"
    api_key_id: int | None = None
    api_key_name: str = ""
    protocol: str
    model: str | None
    stream: bool
    status: str
    http_status: int
    error_message: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int
    created_at: datetime
    updated_at: datetime | None = None
    request_body: Any | None = None
    response_body: Any | None = None


class LogListOut(BaseModel):
    items: list[LogOut]
    total: int
    page: int
    page_size: int


class LogMessageOut(BaseModel):
    role: str
    content: Any = None
    tool_calls: Any = None


class LogMessageListOut(BaseModel):
    items: list[LogMessageOut]
    total: int
    page: int
    page_size: int


class DashboardOut(BaseModel):
    account_count: int
    unhealthy_count: int
    today_requests: int
    today_failures: int
    today_tokens: int
    total_requests: int
    total_tokens: int


class ProviderOut(BaseModel):
    id: str
    label: str
    auth_type: str
    base_url: str
    models: list[str] = Field(default_factory=list)

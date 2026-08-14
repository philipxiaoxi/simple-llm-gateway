from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str


class AccountCreate(BaseModel):
    name: str
    provider: str
    base_url: str | None = None
    api_key: str | None = None
    status: str = "active"


class AccountUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    status: str | None = None


class AccountOut(BaseModel):
    id: int
    name: str
    provider: str
    auth_type: str
    base_url: str
    status: str
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


class KeyCreate(BaseModel):
    name: str
    account_id: int


class KeyUpdate(BaseModel):
    name: str | None = None
    status: str | None = None


class KeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    key: str | None = None
    account_id: int
    account_name: str
    provider: str
    status: str
    created_at: datetime
    last_used_at: datetime | None


class LogOut(BaseModel):
    id: int
    account_id: int
    api_key_id: int
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
    request_body: Any | None = None
    response_body: Any | None = None


class DashboardOut(BaseModel):
    account_count: int
    unhealthy_count: int
    today_requests: int
    today_failures: int


class ProviderOut(BaseModel):
    id: str
    label: str
    auth_type: str
    base_url: str
    models: list[str] = Field(default_factory=list)

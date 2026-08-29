from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


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


class DesktopToolOut(BaseModel):
    id: int
    tool_id: str
    platform: str
    name: str
    description: str
    icon: str | None
    script_name: str
    status: str
    file_name: str | None
    file_size: int | None
    version: str | None
    error_message: str | None
    updated_at: datetime


class DesktopToolRunOut(BaseModel):
    id: int
    tool_id: int
    status: str
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None


class DesktopToolRunDetail(DesktopToolRunOut):
    lines: list[str]


class DesktopToolUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    platform: str | None = None


class DesktopToolCreate(BaseModel):
    tool_id: str
    platform: str
    name: str
    description: str = ""
    icon: str | None = None
    script: str


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
    model_prefix: str | None = None

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
    model_prefix: str | None = None
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
    model_prefix: str | None = None
    models_updated_at: datetime | None = None
    oauth_expires_at: datetime | None = None
    created_at: datetime


class SkillClassificationSettingsOut(BaseModel):
    account_id: int | None = None
    account_name: str | None = None
    model: str | None = None
    enabled: bool = False
    report_account_id: int | None = None
    report_account_name: str | None = None
    report_model: str | None = None
    report_enabled: bool = False


class SkillClassificationSettingsUpdate(BaseModel):
    account_id: int | None = None
    model: str | None = None
    enabled: bool = False
    report_account_id: int | None = None
    report_model: str | None = None
    report_enabled: bool = False


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


class KeyBoundAccountOut(BaseModel):
    id: int
    name: str
    provider: str
    source: str
    status: str
    risk_level: str = "low"
    model_prefix: str | None = None


class KeyCreate(BaseModel):
    name: str
    account_id: int | None = None
    account_ids: list[int] | None = None

    @model_validator(mode="after")
    def require_accounts(self) -> KeyCreate:
        if not self.resolved_account_ids():
            raise ValueError("请至少绑定一个上游账号")
        return self

    def resolved_account_ids(self) -> list[int]:
        if self.account_ids:
            return list(self.account_ids)
        if self.account_id is not None:
            return [self.account_id]
        return []


class KeyUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    account_id: int | None = None
    account_ids: list[int] | None = None

    def resolved_account_ids(self) -> list[int] | None:
        if self.account_ids is not None:
            return list(self.account_ids)
        if self.account_id is not None:
            return [self.account_id]
        return None


class KeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    key: str | None = None
    account_id: int | None = None
    account_name: str
    provider: str
    account_source: str
    risk_level: str
    status: str
    created_at: datetime
    last_used_at: datetime | None
    today_tokens: int = 0
    total_tokens: int = 0
    account_ids: list[int] = Field(default_factory=list)
    accounts: list[KeyBoundAccountOut] = Field(default_factory=list)


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


class DashboardLeaderboardTopOut(BaseModel):
    rank: int | None = None
    name: str
    provider: str = ""
    score: float | None = None
    slug: str = ""


class DashboardBenchmarkTopOut(BaseModel):
    model: str
    account_name: str
    provider: str = ""
    output_tokens_per_second: float
    first_token_ms: float | None = None
    total_ms: float | None = None
    run_id: int | None = None


class DashboardOut(BaseModel):
    account_count: int
    unhealthy_count: int
    today_requests: int
    today_failures: int
    today_tokens: int
    total_requests: int
    total_tokens: int
    benchmark_count: int
    skill_count: int = 0
    key_count: int = 0
    tool_count: int = 0
    agent_count: int = 0
    agent_online_count: int = 0
    leaderboard_top: list[DashboardLeaderboardTopOut] = Field(default_factory=list)
    benchmark_speed_top: list[DashboardBenchmarkTopOut] = Field(default_factory=list)


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None


class SkillFileOut(BaseModel):
    path: str
    size: int
    is_text: bool


class SkillOut(BaseModel):
    id: int
    slug: str
    name: str
    description: str
    category: str
    platforms: list[str] = Field(default_factory=list)
    license: str | None = None
    version: str | None = None
    author: str | None = None
    source_name: str | None = None
    file_count: int
    size_bytes: int
    created_at: datetime
    updated_at: datetime


class SkillDetailOut(SkillOut):
    skill_md: str
    files: list[SkillFileOut] = Field(default_factory=list)
    analysis: "SkillAnalysisOut | None" = None
    analysis_generated_at: datetime | None = None


class SkillAnalysisOut(BaseModel):
    summary: str = ""
    use_cases: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    inputs_outputs: list[str] = Field(default_factory=list)
    trigger_and_workflow: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    permissions_and_risks: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    setup_suggestions: list[str] = Field(default_factory=list)
    example_tasks: list[str] = Field(default_factory=list)
    recommendation: str = ""
    fit_score: int | None = Field(default=None, ge=0, le=100)
    generated_by: str = "gateway-ai"


class SkillSkippedOut(BaseModel):
    name: str
    reason: str


class SkillUploadOut(BaseModel):
    items: list[SkillOut] = Field(default_factory=list)
    created: int = 0
    skipped: list[SkillSkippedOut] = Field(default_factory=list)


class SkillCategoryOut(BaseModel):
    name: str
    count: int


class SkillCategoryManageOut(BaseModel):
    id: int
    name: str
    sort_order: int
    keywords: list[str] = Field(default_factory=list)
    is_protected: bool = False
    count: int = 0
    created_at: datetime


class SkillCategoryCreate(BaseModel):
    name: str
    keywords: list[str] = Field(default_factory=list)


class SkillCategoryUpdate(BaseModel):
    name: str | None = None
    keywords: list[str] | None = None
    sort_order: int | None = None


class SkillCategoryListOut(BaseModel):
    items: list[SkillCategoryManageOut] = Field(default_factory=list)


class SkillListOut(BaseModel):
    items: list[SkillOut]
    total: int
    categories: list[SkillCategoryOut] = Field(default_factory=list)


class ProviderOut(BaseModel):
    id: str
    label: str
    auth_type: str
    base_url: str
    models: list[str] = Field(default_factory=list)


class LeaderboardComponentOut(BaseModel):
    score: float | None = None
    coverage: float | None = None
    metric_count: int | None = None


class LeaderboardLocalMatchOut(BaseModel):
    kind: str
    account_id: int
    account_name: str
    provider: str
    agent_id: str | None = None
    agent_route_id: str | None = None
    matched_model: str


class LeaderboardEntryOut(BaseModel):
    rank: int | None = None
    previous_rank: int | None = None
    rank_change: int | None = None
    slug: str
    name: str
    provider: str
    provider_slug: str | None = None
    released_at: str | None = None
    context_window_tokens: int | None = None
    pricing_kind: str | None = None
    pricing_official_model_id: str | None = None
    input_price_per_million_usd: float | None = None
    output_price_per_million_usd: float | None = None
    input_price_per_million_cny: float | None = None
    output_price_per_million_cny: float | None = None
    price_quote: str | None = None
    pricing_source_name: str | None = None
    pricing_source_url: str | None = None
    score: float | None = None
    uncertainty: float | None = None
    coverage: float | None = None
    confidence: str | None = None
    possible_rank_from: int | None = None
    possible_rank_to: int | None = None
    metric_count: int | None = None
    summary: str | None = None
    components: dict[str, LeaderboardComponentOut] = Field(default_factory=dict)
    local_covered: bool = False
    local_matches: list[LeaderboardLocalMatchOut] = Field(default_factory=list)


class LeaderboardOut(BaseModel):
    source_url: str
    source_page: str
    fetched_at: datetime | None = None
    stale: bool = False
    ttl_seconds: int
    min_refresh_seconds: int
    source_updated_label: str | None = None
    error_message: str | None = None
    unofficial: bool = True
    items: list[LeaderboardEntryOut] = Field(default_factory=list)
    total: int = 0

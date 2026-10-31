from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.header_spoof import normalize_header_spoof


def optional_header_spoof(value: str | None) -> str | None:
    if value is None:
        return None
    return normalize_header_spoof(value)


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


class RoleOut(BaseModel):
    id: int
    name: str
    builtin: bool
    permissions: list[str]
    admin_count: int = 0
    created_at: datetime
    updated_at: datetime


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    permissions: list[str] | None = None


class AdminUserOut(BaseModel):
    id: int
    username: str
    role_id: int | None = None
    role_name: str = ""
    role_builtin: bool = False
    is_enabled: bool = True
    created_at: datetime
    last_login_at: datetime | None = None


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)
    role_id: int
    is_enabled: bool = True


class AdminUserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=64)
    role_id: int | None = None
    is_enabled: bool | None = None


class AdminPasswordReset(BaseModel):
    password: str = Field(min_length=1)


class PermissionCatalogActionOut(BaseModel):
    code: str
    action: str
    label: str


class PermissionCatalogResourceOut(BaseModel):
    resource: str
    label: str
    permissions: list[PermissionCatalogActionOut]


class MeRoleOut(BaseModel):
    id: int
    name: str
    builtin: bool


class MeOut(BaseModel):
    username: str
    role: MeRoleOut | None = None
    permissions: list[str] = Field(default_factory=list)


class AccountCreate(BaseModel):
    name: str
    provider: str
    base_url: str | None = None
    website_url: str | None = None
    api_key: str | None = None
    status: str = "active"
    risk_level: str = "low"
    model_prefix: str | None = None
    header_spoof: str | None = None

    _normalize_website_url = field_validator("website_url")(normalize_website_url)
    _normalize_header_spoof = field_validator("header_spoof")(optional_header_spoof)


class AccountExportRequest(BaseModel):
    password: str


class AccountImportRequest(BaseModel):
    password: str
    payload: dict


class AccountUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    website_url: str | None = None
    api_key: str | None = None
    status: str | None = None
    model_prefix: str | None = None
    risk_level: str | None = None
    header_spoof: str | None = None

    _normalize_website_url = field_validator("website_url")(normalize_website_url)
    _normalize_header_spoof = field_validator("header_spoof")(optional_header_spoof)


class ModelCapsOut(BaseModel):
    id: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    reasoning: bool = False
    reasoning_efforts: list[str] | None = None
    modalities: dict[str, list[str]] = Field(default_factory=lambda: {"input": ["text"], "output": ["text"]})
    source: str = "heuristic"
    overridden: list[str] = Field(default_factory=list)
    overrides: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class CustomModelCreate(BaseModel):
    id: str = Field(min_length=1, max_length=200)


class ModelOverrideUpdate(BaseModel):
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    reasoning: bool | None = None
    reasoning_efforts: list[str] | None = None
    modalities: dict[str, list[str]] | None = None
    enabled: bool | None = None
    clear: bool = False

    @field_validator("reasoning_efforts")
    @classmethod
    def _normalize_efforts(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        items: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in items:
                items.append(text)
        return items

    @field_validator("modalities")
    @classmethod
    def _normalize_modalities(cls, value: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        if value is None:
            return None
        allowed = {"input", "output"}
        extra = set(value) - allowed
        if extra:
            raise ValueError("modalities 只接受 input / output")
        cleaned: dict[str, list[str]] = {}
        for key in ("input", "output"):
            raw = value.get(key)
            if raw is None:
                continue
            if not isinstance(raw, list) or not raw:
                raise ValueError(f"modalities.{key} 必须是非空字符串列表")
            items: list[str] = []
            for item in raw:
                text = str(item).strip()
                if text and text not in items:
                    items.append(text)
            if not items:
                raise ValueError(f"modalities.{key} 必须是非空字符串列表")
            cleaned[key] = items
        if not cleaned:
            raise ValueError("modalities 至少包含 input 或 output")
        return cleaned


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
    models: list[ModelCapsOut] = Field(default_factory=list)
    model_prefix: str | None = None
    header_spoof: str = "none"
    models_updated_at: datetime | None = None
    oauth_expires_at: datetime | None = None
    created_at: datetime


class AccountUsageItemOut(BaseModel):
    kind: str
    id: str | None = None
    name: str
    detail: str
    action: str
    severity: str = "info"


class AccountUsageOut(BaseModel):
    account_id: int
    account_name: str
    has_relations: bool = False
    items: list[AccountUsageItemOut] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


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


class ShareAliasSaveRequest(BaseModel):
    api_key: str
    alias: str
    model: str


class ShareAliasDeleteRequest(BaseModel):
    api_key: str
    alias: str


class ShareAliasRenameRequest(BaseModel):
    api_key: str
    old_alias: str
    new_alias: str


class AdminAliasSaveRequest(BaseModel):
    alias: str
    model: str


class AdminAliasRenameRequest(BaseModel):
    old_alias: str
    new_alias: str


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
    rpm_limit: int | None = Field(default=None, ge=0, le=1_000_000)
    concurrency_limit: int | None = Field(default=None, ge=0, le=1_000_000)

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
    rpm_limit: int | None = Field(default=None, ge=0, le=1_000_000)
    concurrency_limit: int | None = Field(default=None, ge=0, le=1_000_000)

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
    rpm_limit: int = 400
    concurrency_limit: int = 200
    rpm_used: int = 0
    concurrency_used: int = 0
    today_tokens: int = 0
    total_tokens: int = 0
    account_ids: list[int] = Field(default_factory=list)
    accounts: list[KeyBoundAccountOut] = Field(default_factory=list)


class ModelChainOut(BaseModel):
    """模型解析链路：请求时记录 客户端请求名 →（别名）→ 公开模型名 → 上游真实模型。

    历史数据没有记录，requested/alias/public_id 为空、resolved=False，直接展示上游模型名。
    """

    requested: str | None = None
    alias: str | None = None
    public_id: str | None = None
    upstream: str | None = None
    resolved: bool = False


class ReasoningSummaryItemOut(BaseModel):
    length: int
    preview: str


class LogReasoningDetailOut(BaseModel):
    call_id: str
    text: str


class LogMessageDetailOut(BaseModel):
    seq: int
    role: str
    content: Any = None
    tool_calls: Any = None


class ToolCallSummaryOut(BaseModel):
    id: str = ""
    name: str
    arguments_preview: str = ""


class LogExportOut(BaseModel):
    messages: list[LogMessageDetailOut]
    reasoning: dict[str, str]


class LogTurnOut(BaseModel):
    """轮次轮廓：仅含序号与 seq 范围，不携带正文，供折叠列表/弹窗定位使用。"""

    index: int | None = None
    anchor_seq: int
    to_seq: int | None = None
    message_count: int = 0


class LogTurnListOut(BaseModel):
    items: list[LogTurnOut]


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
    # latency_ms 为会话累计耗时；last_latency_ms 为最近一次调用耗时（历史合并行可能为空）
    latency_ms: int
    last_latency_ms: int | None = None
    client_ip: str | None = None
    ip_location: str | None = None
    user_agent: str | None = None
    client_name: str | None = None
    request_headers: dict[str, str] | None = None
    created_at: datetime
    updated_at: datetime | None = None
    request_body: Any | None = None
    response_body: Any | None = None
    # 会话聚合信息：message_count 为消息总数，turn_count 为模型调用轮次（assistant 消息数）
    message_count: int = 0
    turn_count: int = 0
    # 思考内容摘要（按 tool_call_id 归档），仅详情接口返回；全文走 /reasoning/{call_id} 按需取
    reasoning_summary: dict[str, ReasoningSummaryItemOut] | None = None
    # 模型解析链路（客户端请求名 → 别名 → 公开模型 → 上游模型），请求时记录；历史数据只有上游模型名
    model_chain: ModelChainOut | None = None
    # 会话消息是否达到单会话保留上限（storage 截断，仅详情接口返回）
    storage_truncated: bool = False


class LogListOut(BaseModel):
    items: list[LogOut]
    total: int
    page: int
    page_size: int


class LogMessageOut(BaseModel):
    seq: int
    role: str
    content_preview: str | None = None
    content_truncated: bool = False
    content_length: int = 0
    tool_calls: list[ToolCallSummaryOut] | None = None
    # 仅 detail=true（范围查询）时携带全文
    content: Any = None
    tool_calls_full: Any = None


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
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None


class DashboardLeaderboardStatusOut(BaseModel):
    ok: bool = False
    error_message: str | None = None
    fetched_at: datetime | None = None
    total: int = 0


class DashboardBenchmarkTopOut(BaseModel):
    model: str
    account_name: str
    provider: str = ""
    output_tokens_per_second: float
    first_token_ms: float | None = None
    total_ms: float | None = None
    run_id: int | None = None
    created_at: datetime | None = None


class DashboardStatsDayOut(BaseModel):
    """概览图表的一天（上海自然日）。"""

    date: str  # YYYY-MM-DD
    requests: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens: int = 0
    # 当天各模型调用次数：{上游模型名: 次数}
    model_counts: dict[str, int] = Field(default_factory=dict)


class DashboardStatsModelOut(BaseModel):
    """区间内按模型聚合的排行项。"""

    model: str
    requests: int = 0
    tokens: int = 0
    # 占区间总请求数的比例（0~1），前端直接画占比条
    request_share: float = 0.0


class DashboardStatsAccountOut(BaseModel):
    """区间内按上游账号聚合的排行项。"""

    account_name: str
    requests: int = 0
    tokens: int = 0
    request_share: float = 0.0


class DashboardStatsHourOut(BaseModel):
    """上海时区 0~23 点的小时分布。"""

    hour: int
    requests: int = 0


class DashboardStatsSummaryOut(BaseModel):
    """区间汇总，同时用于与上一个等长区间做环比。"""

    requests: int = 0
    failures: int = 0
    success_rate: float = 0.0
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    avg_latency_ms: int | None = None
    p95_latency_ms: int | None = None
    active_days: int = 0
    active_models: int = 0


class DashboardStatsOut(BaseModel):
    days: int
    start_date: str
    end_date: str
    daily: list[DashboardStatsDayOut] = Field(default_factory=list)
    models: list[DashboardStatsModelOut] = Field(default_factory=list)
    accounts: list[DashboardStatsAccountOut] = Field(default_factory=list)
    hourly: list[DashboardStatsHourOut] = Field(default_factory=list)
    summary: DashboardStatsSummaryOut = Field(default_factory=DashboardStatsSummaryOut)
    previous: DashboardStatsSummaryOut = Field(default_factory=DashboardStatsSummaryOut)
    computed_at: datetime | None = None


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
    leaderboard_status: DashboardLeaderboardStatusOut = Field(default_factory=DashboardLeaderboardStatusOut)
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


class SkillBundleCreate(BaseModel):
    name: str
    description: str = ""


class SkillBundleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class SkillBundleOut(BaseModel):
    id: int
    name: str
    description: str
    member_count: int = 0
    created_at: datetime
    updated_at: datetime


class SkillBundleListOut(BaseModel):
    items: list[SkillBundleOut] = Field(default_factory=list)
    total: int = 0


class BundleMemberOut(BaseModel):
    skill_id: int
    added_at: datetime
    missing: bool = False
    skill: SkillOut | None = None


class SkillBundleDetailOut(SkillBundleOut):
    members: list[BundleMemberOut] = Field(default_factory=list)


class BundleMembersAdd(BaseModel):
    skill_ids: list[int] = Field(default_factory=list)


class BundleMemberSkippedOut(BaseModel):
    skill_id: int
    reason: str


class BundleMembersAddOut(BaseModel):
    added: int = 0
    skipped: list[BundleMemberSkippedOut] = Field(default_factory=list)
    members: list[BundleMemberOut] = Field(default_factory=list)


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


class LeaderboardBenchmarkOut(BaseModel):
    model: str
    account_name: str = ""
    provider: str = ""
    output_tokens_per_second: float = 0
    first_token_ms: float | None = None
    total_ms: float | None = None
    created_at: datetime | None = None


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
    max_output_tokens: int | None = None
    pricing_kind: str | None = None
    pricing_official_model_id: str | None = None
    input_price_per_million_usd: float | None = None
    output_price_per_million_usd: float | None = None
    cache_input_price_per_million_cny: float | None = None
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
    benchmark: LeaderboardBenchmarkOut | None = None


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


class JobParamOut(BaseModel):
    key: str
    label: str
    value: int
    min: int
    max: int
    hint: str


class JobOut(BaseModel):
    id: str
    name: str
    description: str
    kind: str
    source_url: str | None = None
    running: bool = False
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_ok: bool | None = None
    last_message: str | None = None
    error_message: str | None = None
    next_run_at: datetime | None = None
    cache_fetched_at: datetime | None = None
    cache_expires_at: datetime | None = None
    cache_ok: bool | None = None
    ttl_seconds: int = 0
    params: list[JobParamOut] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class JobListOut(BaseModel):
    items: list[JobOut] = Field(default_factory=list)


class McpKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    capability_ids: list[str] = Field(min_length=1)
    rpm_limit: int | None = Field(default=None, ge=0, le=1_000_000)
    concurrency_limit: int | None = Field(default=None, ge=0, le=1_000_000)


class McpKeyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    status: str | None = None
    rpm_limit: int | None = Field(default=None, ge=0, le=1_000_000)
    concurrency_limit: int | None = Field(default=None, ge=0, le=1_000_000)


class McpKeyCapabilitiesUpdate(BaseModel):
    capability_ids: list[str] = Field(min_length=1)


class McpCapabilityConfigUpdate(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class McpKeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    status: str
    capability_ids: list[str]
    capability_configs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    rpm_limit: int = 400
    concurrency_limit: int = 200
    rpm_used: int = 0
    concurrency_used: int = 0
    created_at: datetime
    last_used_at: datetime | None = None
    key: str | None = None


class McpCapabilityOut(BaseModel):
    capability_id: str
    name: str
    description: str
    version: str
    category: str = ""
    status: str = "enabled"
    input_schema: dict[str, Any] = Field(default_factory=dict)
    admin_path: str = ""
    icon: str = ""


class McpCallLogOut(BaseModel):
    id: int
    created_at: datetime
    mcp_key_id: int | None
    mcp_key_name: str | None
    mcp_key_prefix: str | None
    capability_id: str
    operation: str
    success: bool
    latency_ms: int
    error_message: str | None = None
    error_type: str | None = None
    transport: str | None = None
    http_status: int | None = None
    client_ip: str | None = None
    client_name: str | None = None


class McpCallLogDetailOut(McpCallLogOut):
    user_agent: str | None = None
    request_headers: str | None = None
    request_body: str | None = None
    response_body: str | None = None


class McpCallLogListOut(BaseModel):
    items: list[McpCallLogOut] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class McpCallStatsBucket(BaseModel):
    key: str
    label: str
    total: int = 0
    success: int = 0
    failed: int = 0
    avg_latency_ms: int = 0


class McpCallStatsOut(BaseModel):
    days: int
    total: int = 0
    success: int = 0
    failed: int = 0
    success_rate: float = 0
    avg_latency_ms: int = 0
    p95_latency_ms: int = 0
    capabilities: list[McpCallStatsBucket] = Field(default_factory=list)
    operations: list[McpCallStatsBucket] = Field(default_factory=list)
    keys: list[McpCallStatsBucket] = Field(default_factory=list)
    daily: list[McpCallStatsBucket] = Field(default_factory=list)
    hourly: list[McpCallStatsBucket] = Field(default_factory=list)


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    scope: str = "public"
    embedding_account_id: int | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    allowed_mcp_key_ids: list[int] | None = None


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    scope: str | None = None
    embedding_account_id: int | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    allowed_mcp_key_ids: list[int] | None = None


class KnowledgeBaseOut(BaseModel):
    id: str
    name: str
    description: str
    scope: str = "public"
    document_count: int = 0
    chunk_count: int = 0
    stale_document_count: int = 0
    signature_mismatch: bool = False
    embedding_account_id: int | None = None
    embedding_account_name: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    allowed_mcp_key_ids: list[int] = Field(default_factory=list)
    active_job_count: int = 0
    last_job_status: str | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeReindexRequest(BaseModel):
    only_stale: bool = False


class KnowledgeEmbeddingAccountOut(BaseModel):
    id: int
    name: str
    provider: str
    source: str
    available: bool
    default_model: str | None = None
    models: list[str] = Field(default_factory=list)


class KnowledgeDocumentCreate(BaseModel):
    text: str = Field(min_length=1)
    source_name: str = "paste.txt"


class KnowledgeDocumentOut(BaseModel):
    id: str
    kb_id: str
    source_name: str
    content_size: int
    chunk_count: int
    vector_status: str
    vector_error: str | None = None
    created_at: datetime


class KnowledgeJobCreate(BaseModel):
    kb_id: str
    text: str = Field(min_length=1)
    source_name: str = "paste.txt"


class KnowledgeReembedJobCreate(BaseModel):
    kb_id: str
    document_id: str


class KnowledgeJobOut(BaseModel):
    id: int
    kb_id: str
    kb_name: str | None = None
    kind: str
    source_name: str
    content_size: int
    chunk_count: int
    status: str
    stage: str
    percent: int
    message: str
    processed_chunks: int
    total_chunks: int
    attempts: int
    max_attempts: int
    document_id: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class KnowledgeJobListOut(BaseModel):
    items: list[KnowledgeJobOut]
    total: int
    counts: dict[str, int]


class KnowledgeSkippedFile(BaseModel):
    name: str
    reason: str


class KnowledgeBatchUploadResult(BaseModel):
    created: int
    job_ids: list[int] = Field(default_factory=list)
    skipped: list[KnowledgeSkippedFile] = Field(default_factory=list)


class KnowledgeSearchRequest(BaseModel):
    kb_id: str | None = None
    kb_ids: list[str] | None = None
    query: str
    mode: str = "hybrid"
    top_k: int = Field(default=5, ge=1, le=20)


class KnowledgeHitOut(BaseModel):
    chunk_id: str
    document_id: str
    kb_id: str = ""
    text: str
    score: float
    source_name: str


class KnowledgeSearchResponse(BaseModel):
    kb_id: str | None = None
    kb_ids: list[str] = Field(default_factory=list)
    mode: str
    degraded: bool = False
    degraded_reason: str | None = None
    hits: list[KnowledgeHitOut] = Field(default_factory=list)


# ---- 安全中心 ----

class SecurityEventOut(BaseModel):
    id: int
    created_at: datetime
    event_type: str
    severity: str
    source: str
    actor: str | None = None
    client_ip: str | None = None
    ip_location: str | None = None
    location_trusted: bool | None = None
    location_rule: str | None = None
    user_agent: str | None = None
    client_name: str | None = None
    client_version: str | None = None
    device_type: str | None = None
    request_headers: dict[str, str] | None = None
    method: str | None = None
    path: str | None = None
    status_code: int | None = None
    outcome: str | None = None
    risk_tags: list[str] = Field(default_factory=list)
    detail: dict[str, Any] | None = None


class SecurityEventDetailOut(SecurityEventOut):
    attempted_password: str | None = None
    password_unavailable: bool = False


class SecurityEventListOut(BaseModel):
    items: list[SecurityEventOut] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class SecurityTrendPoint(BaseModel):
    date: str
    login_success: int = 0
    login_failure: int = 0
    suspicious: int = 0


class SecurityOverviewOut(BaseModel):
    since: datetime
    until: datetime
    login_success: int = 0
    login_failure: int = 0
    suspicious: int = 0
    distinct_ips: int = 0
    severity_counts: dict[str, int] = Field(default_factory=dict)
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    client_counts: list[dict[str, Any]] = Field(default_factory=list)
    top_ips: list[dict[str, Any]] = Field(default_factory=list)
    trend: list[SecurityTrendPoint] = Field(default_factory=list)


class BeaconEvent(BaseModel):
    event: str = "beacon"
    path: str | None = None
    client_time: int | None = None
    timezone: str | None = None
    language: str | None = None
    screen_w: int | None = None
    screen_h: int | None = None
    dpr: float | None = None
    platform: str | None = None
    webdriver: bool | None = None
    action: str | None = None
    target: str | None = None


class SecurityLocationEntry(BaseModel):
    country: str = ""
    province: str = ""
    city: str = ""
    raw: str = ""
    enabled: bool = True


class SecuritySettingsOut(BaseModel):
    location_check_enabled: bool = True
    trusted_locations: list[SecurityLocationEntry] = Field(default_factory=list)
    ip_allowlist: list[str] = Field(default_factory=list)


class SecuritySettingsUpdate(BaseModel):
    location_check_enabled: bool = True
    trusted_locations: list[SecurityLocationEntry] = Field(default_factory=list)
    ip_allowlist: list[str] = Field(default_factory=list)

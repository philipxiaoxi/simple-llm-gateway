from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.clock import utcnow


class Base(DeclarativeBase):
    pass


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UpstreamAccount(Base):
    __tablename__ = "upstream_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="upstream", nullable=False)
    agent_route_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    auth_type: Mapped[str] = mapped_column(String(16), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    website_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), default="low", nullable=False)
    last_probe_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_probe_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_probe_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    quota_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    quota_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    models_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    models_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    model_prefix: Mapped[str | None] = mapped_column(String(32), nullable=True)
    header_spoof: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    oauth_token: Mapped[OAuthToken | None] = relationship(back_populates="account", uselist=False)
    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="account")
    key_links: Mapped[list[ApiKeyAccount]] = relationship(back_populates="account")


class GatewayAgent(Base):
    __tablename__ = "gateway_agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="offline", nullable=False)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_disconnected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    routes: Mapped[list[GatewayAgentRoute]] = relationship(
        back_populates="agent", cascade="all, delete-orphan", order_by="GatewayAgentRoute.id"
    )


class GatewayAgentRoute(Base):
    __tablename__ = "gateway_agent_routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("gateway_agents.id"), index=True, nullable=False)
    route_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    models_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    models_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    agent: Mapped[GatewayAgent] = relationship(back_populates="routes")


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("upstream_accounts.id"), unique=True, nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scope: Mapped[str | None] = mapped_column(String(512), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    account: Mapped[UpstreamAccount] = relationship(back_populates="oauth_token")


class OAuthState(Base):
    __tablename__ = "oauth_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("upstream_accounts.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("key_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("upstream_accounts.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    account: Mapped[UpstreamAccount | None] = relationship(back_populates="api_keys")
    account_links: Mapped[list[ApiKeyAccount]] = relationship(
        back_populates="api_key",
        cascade="all, delete-orphan",
        order_by="ApiKeyAccount.sort_order",
    )
    aliases: Mapped[list[ModelAlias]] = relationship(
        back_populates="api_key",
        cascade="all, delete-orphan",
        order_by="ModelAlias.id",
    )


class ApiKeyAccount(Base):
    __tablename__ = "api_key_accounts"
    __table_args__ = (UniqueConstraint("api_key_id", "account_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id", ondelete="CASCADE"), index=True, nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("upstream_accounts.id"), index=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    api_key: Mapped[ApiKey] = relationship(back_populates="account_links")
    account: Mapped[UpstreamAccount] = relationship(back_populates="key_links")


class ModelAlias(Base):
    __tablename__ = "model_aliases"
    __table_args__ = (UniqueConstraint("api_key_id", "alias"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id", ondelete="CASCADE"), index=True, nullable=False)
    alias: Mapped[str] = mapped_column(String(128), nullable=False)
    model_public_id: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    api_key: Mapped[ApiKey] = relationship(back_populates="aliases")


class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_source: Mapped[str] = mapped_column(String(16), default="upstream", nullable=False)
    api_key_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    api_key_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stream: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    request_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    reasoning_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)

    account: Mapped[UpstreamAccount | None] = relationship(
        "UpstreamAccount",
        primaryjoin="foreign(RequestLog.account_id) == UpstreamAccount.id",
        viewonly=True,
    )
    api_key: Mapped[ApiKey | None] = relationship(
        "ApiKey",
        primaryjoin="foreign(RequestLog.api_key_id) == ApiKey.id",
        viewonly=True,
    )
    messages: Mapped[list[RequestLogMessage]] = relationship(
        back_populates="log",
        cascade="all, delete-orphan",
        order_by="RequestLogMessage.seq",
    )


class RequestLogMessage(Base):
    __tablename__ = "request_log_messages"
    __table_args__ = (UniqueConstraint("log_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    log_id: Mapped[int] = mapped_column(ForeignKey("request_logs.id", ondelete="CASCADE"), index=True, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    log: Mapped[RequestLog] = relationship(back_populates="messages")


class BenchmarkRun(Base):
    __tablename__ = "benchmark_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    results: Mapped[list[BenchmarkResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="BenchmarkResult.id"
    )


class BenchmarkResult(Base):
    __tablename__ = "benchmark_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("benchmark_runs.id", ondelete="CASCADE"), index=True, nullable=False)
    account_id: Mapped[int] = mapped_column(Integer, nullable=False)
    account_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    timeout: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_token_ms: Mapped[float | None] = mapped_column(nullable=True)
    total_ms: Mapped[float | None] = mapped_column(nullable=True)
    output_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens_per_second: Mapped[float | None] = mapped_column(nullable=True)
    preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[BenchmarkRun] = relationship(back_populates="results")


class SkillCategory(Base):
    __tablename__ = "skill_categories"
    __table_args__ = (UniqueConstraint("name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    keywords_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_protected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class SkillClassificationSettings(Base):
    __tablename__ = "skill_classification_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("upstream_accounts.id"), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    report_account_id: Mapped[int | None] = mapped_column(ForeignKey("upstream_accounts.id"), nullable=True)
    report_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    report_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="其他", nullable=False)
    platforms_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    license: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    storage_dir: Mapped[str] = mapped_column(String(160), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skill_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    analysis_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class SkillBundle(Base):
    __tablename__ = "skill_bundles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class SkillBundleMember(Base):
    __tablename__ = "skill_bundle_members"
    __table_args__ = (UniqueConstraint("bundle_id", "skill_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bundle_id: Mapped[int] = mapped_column(ForeignKey("skill_bundles.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class DesktopTool(Base):
    __tablename__ = "desktop_tools"
    __table_args__ = (UniqueConstraint("tool_id", "platform"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_id: Mapped[str] = mapped_column(String(80), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    icon: Mapped[str | None] = mapped_column(String(256), nullable=True)
    script_name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="not_downloaded", nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class DesktopToolRun(Base):
    __tablename__ = "desktop_tool_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_id: Mapped[int] = mapped_column(Integer, ForeignKey("desktop_tools.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DesktopToolRunLog(Base):
    __tablename__ = "desktop_tool_run_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("desktop_tool_runs.id"), index=True, nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    line: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class LeaderboardSnapshot(Base):
    __tablename__ = "leaderboard_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    entries_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    source_updated_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ContentAuditFinding(Base):
    __tablename__ = "content_audit_findings"
    __table_args__ = (
        UniqueConstraint(
            "log_id",
            "message_seq",
            "category",
            "rule_key",
            "start_offset",
            name="uq_content_audit_finding",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    log_id: Mapped[int] = mapped_column(
        ForeignKey("request_logs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    message_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    lexicon_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rule_key: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    api_key_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    api_key_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class ContentAuditScan(Base):
    __tablename__ = "content_audit_scans"

    log_id: Mapped[int] = mapped_column(
        ForeignKey("request_logs.id", ondelete="CASCADE"), primary_key=True
    )
    last_scanned_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_message_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    finding_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ok", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class VoiceRoom(Base):
    """语音输入房间：手机与电脑通过房间配对，1 个房间 = N 个手机 + M 台电脑。"""

    __tablename__ = "voice_rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    join_code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    pin_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # ASR（实时语音识别）配置：供应商先写死阿里云，但字段保留以便将来切换
    asr_provider: Mapped[str] = mapped_column(String(32), default="aliyun_dashscope", nullable=False)
    asr_model: Mapped[str] = mapped_column(String(64), default="qwen-audio-3.0-asr-flash-streaming", nullable=False)
    disfluency_removal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_recording_seconds: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    # AI 纠错配置：off 只用 ASR / error_fix 只纠错 / rewrite 允许书面化改写
    polish_mode: Mapped[str] = mapped_column(String(16), default="error_fix", nullable=False)
    polish_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    polish_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    polish_system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    polish_temperature: Mapped[float] = mapped_column(Float, default=0.2, nullable=False)
    phone_token_ttl_seconds: Mapped[int] = mapped_column(Integer, default=43200, nullable=False)
    log_partials: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class VoiceClient(Base):
    """房间成员：手机（phone）或电脑（desktop），按 client_uid 去重。"""

    __tablename__ = "voice_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_pk: Mapped[int] = mapped_column(ForeignKey("voice_rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    client_uid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="offline", nullable=False)
    insert_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_disconnected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class VoiceSession(Base):
    """一次录音会话：按下说话 → 松开（或超时/丢弃）。"""

    __tablename__ = "voice_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_uid: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    room_pk: Mapped[int] = mapped_column(ForeignKey("voice_rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    client_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    asr_model: Mapped[str] = mapped_column(String(64), nullable=False)
    audio_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    audio_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    frame_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sentence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    asr_usage_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_partial_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class VoiceSegment(Base):
    """文本段：语音输入的最小投递/替换单位，也是日志主体。"""

    __tablename__ = "voice_segments"
    __table_args__ = (UniqueConstraint("room_pk", "seq", name="uq_voice_segment_room_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seg_uid: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    session_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    room_pk: Mapped[int] = mapped_column(ForeignKey("voice_rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    rev: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="partial", nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    polished_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    polish_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    polish_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    polish_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    polish_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    polish_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    asr_begin_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    asr_end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deliver_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ack_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_ack_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class VoiceEvent(Base):
    """房间事件时间线：加入/离开、录音、识别、纠错、下发、回执、放弃。"""

    __tablename__ = "voice_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_pk: Mapped[int] = mapped_column(ForeignKey("voice_rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    session_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    segment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    level: Mapped[str] = mapped_column(String(8), default="info", nullable=False)
    message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class McpKey(Base):
    __tablename__ = "mcp_keys"
    __table_args__ = (UniqueConstraint("key_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    capabilities: Mapped[list[McpKeyCapability]] = relationship(
        back_populates="mcp_key",
        cascade="all, delete-orphan",
    )


class McpKeyCapability(Base):
    __tablename__ = "mcp_key_capabilities"
    __table_args__ = (UniqueConstraint("mcp_key_id", "capability_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mcp_key_id: Mapped[int] = mapped_column(ForeignKey("mcp_keys.id", ondelete="CASCADE"), index=True, nullable=False)
    capability_id: Mapped[str] = mapped_column(String(64), nullable=False)

    mcp_key: Mapped[McpKey] = relationship(back_populates="capabilities")


class McpCallLog(Base):
    __tablename__ = "mcp_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    mcp_key_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    mcp_key_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mcp_key_prefix: Mapped[str | None] = mapped_column(String(32), nullable=True)
    capability_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    request_meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    scope: Mapped[str] = mapped_column(String(16), default="public", nullable=False)
    embedding_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    embedding_model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    documents: Mapped[list[KnowledgeDocument]] = relationship(
        back_populates="base",
        cascade="all, delete-orphan",
    )


class KnowledgeBaseKey(Base):
    """restricted 知识库的 MCP Key 白名单。"""

    __tablename__ = "knowledge_base_keys"
    __table_args__ = (UniqueConstraint("kb_id", "mcp_key_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kb_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True, nullable=False
    )
    mcp_key_id: Mapped[int] = mapped_column(
        ForeignKey("mcp_keys.id", ondelete="CASCADE"), index=True, nullable=False
    )


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True, nullable=False)
    source_name: Mapped[str] = mapped_column(String(256), default="paste.txt", nullable=False)
    content_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    vector_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    vector_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    base: Mapped[KnowledgeBase] = relationship(back_populates="documents")
    chunks: Mapped[list[KnowledgeChunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kb_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(String(256), default="", nullable=False)

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class KnowledgeIngestJob(Base):
    """知识库采集任务：入库（ingest）或重新向量化（reembed）。

    原文落盘保存，支持失败重试；进度写入本表，前端轮询展示。
    """

    __tablename__ = "knowledge_ingest_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="ingest", nullable=False)
    source_name: Mapped[str] = mapped_column(String(256), default="paste.txt", nullable=False)
    content_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str] = mapped_column(String(256), default="排队中", nullable=False)
    processed_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class DocParseJob(Base):
    """办公文档转 Markdown 任务。源文件与结果落在 DOC_PARSE_PATH。"""

    __tablename__ = "docparse_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mcp_key_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    created_by: Mapped[str] = mapped_column(String(16), default="admin", nullable=False)
    source_name: Mapped[str] = mapped_column(String(256), default="upload.bin", nullable=False)
    source_ext: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    source_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str] = mapped_column(String(256), default="排队中", nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    markdown_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ingest_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    purged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

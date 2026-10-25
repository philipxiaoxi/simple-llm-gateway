from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_APP_SECRET_KEYS = frozenset(
    {
        "dev-only-change-me",
        "change-me-to-a-long-random-string",
        "replace-with-a-long-random-string",
    }
)
INSECURE_ADMIN_PASSWORDS = frozenset(
    {
        "changeme",
        "replace-with-a-strong-password",
        "admin",
        "password",
        "12345678",
    }
)
MIN_APP_SECRET_KEY_LENGTH = 16
MIN_ADMIN_PASSWORD_LENGTH = 8


def validate_app_secret_key(secret_key: str) -> None:
    stripped = (secret_key or "").strip()
    if stripped in INSECURE_APP_SECRET_KEYS or len(stripped) < MIN_APP_SECRET_KEY_LENGTH:
        raise RuntimeError("APP_SECRET_KEY 未设置或仍是示例值。请改成至少 16 位的随机字符串后启动。")


def validate_bootstrap_admin_password(password: str) -> None:
    if password in INSECURE_ADMIN_PASSWORDS or len(password) < MIN_ADMIN_PASSWORD_LENGTH:
        raise RuntimeError("ADMIN_PASSWORD 未设置、过短或仍是示例值。首次启动请设置至少 8 位的密码。")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_secret_key: str = "dev-only-change-me"
    admin_username: str = "admin"
    admin_password: str = "changeme"
    database_path: str = "data/gateway.db"
    app_base_url: str = "http://127.0.0.1:8000"
    request_timeout_seconds: int = 120
    # 额度/余额查询走独立短超时：上游不可达时管理页要快速失败，而不是挂到 request_timeout
    quota_timeout_seconds: int = 20
    local_agent_token: str = ""
    # 请求日志 IP 归属地。留空则只记录 IP，不查位置。
    ip_geo_url: str = "http://ip-api.com/json/{ip}?lang=zh-CN&fields=status,country,regionName,city,message"
    # 只有直连地址落在这些网段时，才信任 X-Forwarded-For / X-Real-IP。逗号分隔，支持前缀或 CIDR。
    trusted_proxy_cidrs: str = "172.20.0.0/16"
    quota_refresh_interval_seconds: int = 3600
    jwt_expire_days: int = 7
    frontend_dist: str = ""
    skills_path: str = ""
    tools_path: str = ""
    tools_download_timeout_seconds: int = 3600
    # AIHOT 模型榜（原 aihot.virxact.com 已 301 到新域名，直接用新域名少一跳）
    aihot_leaderboard_url: str = "https://aihot.news/leaderboard"
    # 榜单数据走页面同源的单次取数端点（React Router .data），是结构化 JSON，
    # 比解析渲染后的 HTML/RSC 稳；页面地址仍用于展示与「查看原榜」链接。
    aihot_leaderboard_data_url: str = "https://aihot.news/leaderboard.data"
    aihot_leaderboard_ttl_seconds: int = 43200
    aihot_leaderboard_min_refresh_seconds: int = 60
    # 概览图表落库后的刷新间隔（秒）。请求只读快照，后台任务按这个间隔重算。
    dashboard_stats_refresh_seconds: int = 1800
    # 定时任务面板的 DB 快照默认缓存 30 分钟；设为 0 即关闭。
    admin_jobs_cache_seconds: int = 1800
    # 连接持有超过这个秒数就打 [db-hold]，0 关闭。
    db_long_hold_seconds: float = 5
    # 请求超过这个秒数就打 [slow]，0 关闭。
    slow_request_seconds: float = 2
    xai_oauth_client_id: str = "b1a00492-073a-47ea-816f-4c329264a828"
    xai_oauth_authorize_url: str = "https://auth.x.ai/oauth2/authorize"
    xai_oauth_token_url: str = "https://auth.x.ai/oauth2/token"
    xai_oauth_redirect_uri: str = "http://127.0.0.1:56121/callback"
    xai_oauth_scope: str = "openid profile email offline_access grok-cli:access api:access"
    # ---- 语音输入（手机 → 电脑）----
    # 阿里云百炼（DashScope）API Key，格式 sk-ws-…，只存服务端，绝不下发到浏览器
    aliyun_dashscope_api_key: str = ""
    aliyun_asr_ws_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    aliyun_asr_model: str = "qwen-audio-3.0-asr-flash-streaming"
    # 桌面客户端共享令牌（房间级令牌优先，这里用于人工配置场景）
    voice_desktop_token: str = ""
    voice_polish_timeout_seconds: int = 12
    # 纠错输出的 token 预算。推理模型会先消耗大量 token 思考，512 会导致 content 为空（实测），
    # 所以默认给到 2048。
    voice_polish_max_tokens: int = 2048
    voice_max_recording_seconds: int = 120
    voice_polish_concurrency: int = 4
    voice_event_retention_days: int = 30
    voice_public_base_url: str = ""
    # ---- MCP 能力平面 / 知识库 ----
    mcp_embedding_base_url: str = ""
    mcp_embedding_api_key: str = ""
    mcp_embedding_model: str = "text-embedding-3-small"
    mcp_chroma_path: str = ""
    mcp_knowledge_max_bytes: int = 2 * 1024 * 1024
    # 目录批量上传：单次最多文件数与总字节
    mcp_knowledge_batch_max_files: int = 200
    mcp_knowledge_batch_max_total_bytes: int = 64 * 1024 * 1024
    mcp_chunk_size: int = 700
    mcp_chunk_overlap: int = 100
    mcp_capability_timeout_seconds: int = 60
    # 智谱 embedding 单次数组最多 64；默认 16 便于进度更细、超时更稳
    mcp_embedding_batch_size: int = 16
    mcp_embedding_dimensions: int = 32
    # 知识库采集任务：原文落盘目录与并发
    mcp_knowledge_jobs_path: str = ""
    mcp_knowledge_job_concurrency: int = 1
    mcp_knowledge_job_max_attempts: int = 3
    # 检索命中文本回传上限（字符），避免大块吃满上下文
    mcp_knowledge_max_hit_chars: int = 2000
    # 已结束任务与调用日志的保留天数
    mcp_knowledge_job_retention_days: int = 30
    mcp_call_log_retention_days: int = 30
    # 文档转 Markdown
    doc_parse_path: str = ""
    doc_parse_max_bytes: int = 30 * 1024 * 1024
    doc_parse_max_pdf_pages: int = 200
    doc_parse_timeout_seconds: int = 180
    doc_parse_retention_days: int = 7
    doc_parse_ocr: int = 0
    doc_parse_mcp_max_bytes: int = 20 * 1024 * 1024
    # 站点部署（静态资源托管）
    site_deploy_path: str = ""
    site_default_entry: str = "index.html"
    site_max_archive_bytes: int = 50 * 1024 * 1024
    site_max_total_bytes: int = 200 * 1024 * 1024
    site_max_file_bytes: int = 50 * 1024 * 1024
    site_max_files: int = 5000
    site_max_ratio: int = 100
    site_max_versions: int = 10
    site_retention_days: int = 30
    site_mcp_max_bytes: int = 10 * 1024 * 1024
    # 抖音视频/图集下载（douyin 能力）
    douyin_media_path: str = ""
    douyin_max_media_bytes: int = 5 * 1024 * 1024 * 1024
    douyin_max_item_bytes: int = 5 * 1024 * 1024 * 1024
    douyin_max_items: int = 60
    douyin_http_timeout_seconds: int = 20
    douyin_mcp_max_wait_seconds: int = 45
    douyin_max_redirects: int = 5
    douyin_download_token_ttl_seconds: int = 3600
    # 0 表示永久保留，不做自动清理
    douyin_retention_days: int = 0
    douyin_max_concurrent_per_key: int = 2
    douyin_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    # TikHub 托管解析 API：无本地依赖，平台侧反爬由服务方处理
    douyin_tikhub_base_url: str = "https://api.tikhub.io"
    douyin_tikhub_api_key: str = ""
    douyin_tikhub_timeout_seconds: int = 60
    # TikHub 凭据：抖音下载与资讯收集共享。中立命名，优先级高于 DOUYIN_* / INFO_*
    tikhub_base_url: str = ""
    tikhub_api_key: str = ""
    # Archify 图表生成（diagram 能力）
    diagram_enabled: bool = True
    archify_home: str = ""
    diagram_node_binary: str = "node"
    diagram_render_timeout_seconds: int = 60
    diagram_render_concurrency: int = 2
    diagram_max_source_bytes: int = 256 * 1024
    diagram_default_quality: str = "showcase"
    # ---- 在线浏览器（browser 能力）----
    browser_enabled: bool = False
    browser_media_path: str = ""
    browser_headless: bool = True
    browser_max_sessions: int = 10
    browser_max_sessions_per_key: int = 2
    browser_idle_ttl_seconds: int = 900
    browser_nav_timeout_ms: int = 30000
    browser_viewport_width: int = 1280
    browser_viewport_height: int = 720
    browser_max_screenshot_bytes: int = 5 * 1024 * 1024
    browser_screenshot_ttl_seconds: int = 86400
    # 空表示允许任意公网主机；配置后仅允许列表内主机及其子域
    browser_allowed_hosts: str = ""
    browser_blocked_hosts: str = ""
    browser_worker_url: str = ""
    browser_user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    # ---- 资讯收集（Telegram 等公开渠道 → 瀑布流浏览）----
    # 媒体目录留空时与数据库同级（Docker 下即 /data/info，已被 ./data 卷覆盖）
    info_media_path: str = ""
    info_tick_seconds: int = 60
    info_backfill_limit: int = 30
    info_poll_limit: int = 20
    # 单轮增量最多翻多少页（每页 info_poll_limit 条）：一轮把积压拉平，
    # 又给上游限流留余量；拉不完的下一轮接着拉（游标只推进到已处理处）。
    info_incremental_max_pages: int = 5
    info_max_concurrent_sources: int = 2
    info_max_concurrent_downloads: int = 3
    info_max_item_bytes: int = 200 * 1024 * 1024
    info_max_media_per_item: int = 20
    info_media_token_ttl_seconds: int = 43200
    # 0 表示媒体永久保留，不做自动清理
    info_retention_days: int = 0
    info_http_timeout_seconds: int = 30
    info_max_redirects: int = 5
    info_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    # TikHub 凭据兜底：优先读数据库里与抖音下载共用的加密配置
    info_tikhub_base_url: str = ""
    info_tikhub_api_key: str = ""
    # AI 判定 worker：轮询间隔、每轮批量、并发
    info_ai_tick_seconds: int = 30
    info_ai_batch_size: int = 5
    info_ai_max_concurrent: int = 2
    # 微信全文补全 worker：每轮批量、轮询间隔、单条最大尝试次数
    info_wechat_detail_batch_size: int = 3
    info_wechat_detail_interval_seconds: int = 10
    info_wechat_detail_max_attempts: int = 3
    # 正文 HTML 清洗后保留的最大字符数，超出则不存（详情页退回纯文本）
    info_wechat_html_max_chars: int = 300_000
    # 单篇公众号正文最多转存多少张图（公众号长图文常超 Telegram 的通用上限）
    info_wechat_max_body_images: int = 80
    # ---- 资讯上报（Agent 经 MCP/REST 上送资讯，上报即公开）----
    info_report_enabled: bool = True
    info_report_max_text_chars: int = 20000
    info_report_max_title_chars: int = 256
    # REST multipart 单文件上限
    info_report_max_file_bytes: int = 20 * 1024 * 1024
    # MCP base64 单文件解码后上限（比 REST 小，避免 JSON 过大）
    info_report_max_mcp_file_bytes: int = 5 * 1024 * 1024
    info_report_max_files_per_item: int = 9
    info_report_max_item_bytes: int = 60 * 1024 * 1024
    info_report_batch_max_items: int = 20
    info_report_rate_per_minute: int = 30
    # ---- 离线下载缓存（下载一次，服务器留一份，之后本机直接命中）----
    # 目录留空时与数据库同级（data/offline）
    offline_cache_path: str = ""
    # 缓存总字节上限，超出后按最久未访问淘汰
    offline_cache_max_bytes: int = 5 * 1024 * 1024 * 1024
    # 后台缓存并发数（大包下载不影响请求，串行/少量并发即可）
    offline_cache_concurrency: int = 2
    # ---- 图片 / 视频生成（gsai 等供应商）----
    # 作品与参考文件落盘目录，留空时与数据库同级（data/generation）
    generation_media_path: str = ""
    # 后台轮询/转存循环间隔
    generation_tick_seconds: int = 5
    # 单个参考文件大小上限
    generation_max_input_bytes: int = 30 * 1024 * 1024
    # 单个生成结果文件大小上限
    generation_result_max_bytes: int = 512 * 1024 * 1024
    # 生成任务提交/轮询失败的最大重试次数
    generation_max_attempts: int = 3
    # 调用供应商接口的超时
    generation_http_timeout_seconds: int = 60
    # 作品与参考文件地址的签名令牌有效期
    generation_token_ttl_seconds: int = 43200
    # MCP 生成能力提交后内联等待完成的秒数上限
    generation_mcp_max_wait_seconds: int = 60
    # ---- 安全中心（登录审计 / 异常请求标记 / 前端埋点）----
    security_audit_enabled: bool = True
    security_beacon_enabled: bool = True
    security_event_retention_days: int = 90
    security_dedupe_window_seconds: int = 30
    security_max_password_len: int = 256
    security_beacon_rate_limit: int = 60
    security_auth_fail_threshold: int = 10
    security_auth_fail_window_seconds: int = 300
    # 登录锁定按来源 IP 计数；这些 IP/CIDR 豁免（逗号分隔）
    security_login_whitelist: str = ""
    # ---- 网关限速（/mcp 与 /v1 等，按 Key 的 RPM 与并发）----
    # Key 默认额度，可在后台按 Key 覆盖；0 表示不限
    security_gateway_key_rpm: int = 400
    security_gateway_key_concurrency: int = 200
    # 未携带有效 Key 的请求按来源 IP 计数，防止 Key 枚举；0 表示不限
    security_gateway_ip_rpm: int = 300
    # 安全联系人：配置后 /.well-known/security.txt 才会提供
    security_contact: str = ""

    @property
    def database_url(self) -> str:
        if self.database_path == ":memory:":
            return "sqlite:///:memory:"
        path = Path(self.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path.resolve()}"

    @property
    def resolved_skills_path(self) -> Path:
        if self.skills_path:
            path = Path(self.skills_path)
        elif self.database_path == ":memory:":
            path = Path("data") / "skills"
        else:
            path = Path(self.database_path).expanduser().resolve().parent / "skills"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def resolved_tools_path(self) -> Path:
        path = Path(self.tools_path) if self.tools_path else Path(self.database_path).expanduser().resolve().parent / "tools"
        (path / "scripts").mkdir(parents=True, exist_ok=True)
        (path / "downloads").mkdir(parents=True, exist_ok=True)
        return path

    @property
    def data_dir(self) -> Path:
        """数据目录：与数据库同级。Docker 下 DATABASE_PATH=/data/...，即 /data。"""
        if self.database_path == ":memory:":
            return Path("data").resolve()
        return Path(self.database_path).expanduser().resolve().parent

    def _resolve_data_path(self, value: str, default_name: str) -> Path:
        """解析知识库数据路径。

        相对路径按数据目录解析而非进程工作目录，避免容器里落到 WORKDIR 导致数据不在卷内。
        """
        candidate = Path(value).expanduser() if value else self.data_dir / default_name
        if not candidate.is_absolute():
            candidate = self.data_dir / candidate
        path = candidate.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def resolved_knowledge_jobs_path(self) -> Path:
        return self._resolve_data_path(self.mcp_knowledge_jobs_path, "knowledge_jobs")

    @property
    def resolved_chroma_path(self) -> Path:
        return self._resolve_data_path(self.mcp_chroma_path, "chroma")

    @property
    def resolved_docparse_path(self) -> Path:
        return self._resolve_data_path(self.doc_parse_path, "docparse")

    @property
    def resolved_site_deploy_path(self) -> Path:
        return self._resolve_data_path(self.site_deploy_path, "sites")

    @property
    def resolved_douyin_media_path(self) -> Path:
        return self._resolve_data_path(self.douyin_media_path, "douyin")

    @property
    def resolved_info_media_path(self) -> Path:
        return self._resolve_data_path(self.info_media_path, "info")

    @property
    def resolved_browser_media_path(self) -> Path:
        return self._resolve_data_path(self.browser_media_path, "browser")

    @property
    def resolved_offline_cache_path(self) -> Path:
        return self._resolve_data_path(self.offline_cache_path, "offline")

    @property
    def resolved_generation_path(self) -> Path:
        return self._resolve_data_path(self.generation_media_path, "generation")

    @property
    def resolved_archify_home(self) -> Path:
        """vendored Archify 渲染器目录。可用 ARCHIFY_HOME 覆盖。"""
        if self.archify_home:
            return Path(self.archify_home).expanduser().resolve()
        return Path(__file__).resolve().parent / "capabilities" / "diagram" / "vendor" / "archify"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    get_settings.cache_clear()

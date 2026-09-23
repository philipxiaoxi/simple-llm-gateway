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
    quota_refresh_interval_seconds: int = 3600
    jwt_expire_days: int = 7
    frontend_dist: str = ""
    skills_path: str = ""
    tools_path: str = ""
    tools_download_timeout_seconds: int = 3600
    # AIHOT 模型榜（原 aihot.virxact.com 已 301 到新域名，直接用新域名少一跳）
    aihot_leaderboard_url: str = "https://aihot.news/leaderboard"
    aihot_leaderboard_ttl_seconds: int = 43200
    aihot_leaderboard_min_refresh_seconds: int = 60
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


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    get_settings.cache_clear()

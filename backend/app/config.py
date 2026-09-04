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
    local_agent_token: str = ""
    quota_refresh_interval_seconds: int = 3600
    jwt_expire_days: int = 7
    frontend_dist: str = ""
    skills_path: str = ""
    tools_path: str = ""
    tools_download_timeout_seconds: int = 3600
    aihot_leaderboard_url: str = "https://aihot.virxact.com/leaderboard"
    aihot_leaderboard_ttl_seconds: int = 43200
    aihot_leaderboard_min_refresh_seconds: int = 60
    xai_oauth_client_id: str = "b1a00492-073a-47ea-816f-4c329264a828"
    xai_oauth_authorize_url: str = "https://auth.x.ai/oauth2/authorize"
    xai_oauth_token_url: str = "https://auth.x.ai/oauth2/token"
    xai_oauth_redirect_uri: str = "http://127.0.0.1:56121/callback"
    xai_oauth_scope: str = "openid profile email offline_access grok-cli:access api:access"
    aliyun_asr_api_key: str = ""
    aliyun_asr_ws_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    aliyun_asr_model: str = "qwen-audio-3.0-asr-flash-streaming"
    voice_stt_base_url: str = ""
    voice_stt_api_key: str = ""
    voice_stt_model: str = "whisper-1"
    voice_stt_language: str = ""
    voice_llm_base_url: str = ""
    voice_llm_api_key: str = ""
    voice_llm_model: str = "gpt-4o-mini"
    voice_llm_prompt: str = ""
    voice_http_timeout_seconds: int = 120

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    get_settings.cache_clear()

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_secret_key: str = "dev-only-change-me"
    admin_username: str = "admin"
    admin_password: str = "changeme"
    database_path: str = "data/gateway.db"
    app_base_url: str = "http://127.0.0.1:8000"
    request_timeout_seconds: int = 120
    jwt_expire_days: int = 7
    frontend_dist: str = ""
    xai_oauth_client_id: str = "grok-cli"
    xai_oauth_authorize_url: str = "https://accounts.x.ai/oauth/authorize"
    xai_oauth_token_url: str = "https://accounts.x.ai/oauth/token"
    xai_oauth_scope: str = "openid profile email offline_access grok-cli:access api:access"

    @property
    def database_url(self) -> str:
        if self.database_path == ":memory:":
            return "sqlite:///:memory:"
        path = Path(self.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path.resolve()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    get_settings.cache_clear()

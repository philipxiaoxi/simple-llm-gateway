from __future__ import annotations

from datetime import datetime, timedelta

from app.config import get_settings
from app.crypto import decrypt_secret
from app.models import UpstreamAccount


class CredentialError(Exception):
    def __init__(self, message: str, status_code: int = 403) -> None:
        super().__init__(message)
        self.status_code = status_code


def get_upstream_credential(account: UpstreamAccount, allow_expired: bool = False) -> str | None:
    settings = get_settings()
    if account.auth_type == "api_key":
        if not account.api_key_encrypted:
            return None
        return decrypt_secret(account.api_key_encrypted, settings.app_secret_key)

    token = account.oauth_token
    if token is not None:
        if (
            allow_expired
            or token.expires_at is None
            or token.expires_at > datetime.utcnow() + timedelta(seconds=30)
        ):
            return decrypt_secret(token.access_token_encrypted, settings.app_secret_key)
    if account.api_key_encrypted:
        return decrypt_secret(account.api_key_encrypted, settings.app_secret_key)
    return None


def require_upstream_credential(account: UpstreamAccount) -> str:
    if account.status != "active":
        raise CredentialError("绑定的上游账号已停用", 403)
    credential = get_upstream_credential(account, allow_expired=True)
    if not credential:
        raise CredentialError("上游账号尚未配置密钥或授权", 403)
    return credential

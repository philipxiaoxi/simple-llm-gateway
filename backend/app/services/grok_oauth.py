from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.crypto import decrypt_secret, encrypt_secret
from app.db import get_session_factory
from app.models import OAuthState, OAuthToken, UpstreamAccount

OAUTH_REFRESH_INTERVAL_SECONDS = 10 * 60
OAUTH_REFRESH_SOON_SECONDS = 20 * 60


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def cleanup_expired_oauth_states(db: Session) -> int:
    result = db.execute(delete(OAuthState).where(OAuthState.expires_at < datetime.utcnow()))
    return result.rowcount or 0


def is_loopback_redirect(redirect_uri: str) -> bool:
    host = (urlparse(redirect_uri).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def parse_oauth_callback(
    callback_url: str | None = None,
    code: str | None = None,
    state: str | None = None,
) -> tuple[str, str]:
    parsed_code = (code or "").strip() or None
    parsed_state = (state or "").strip() or None
    if callback_url:
        query = parse_qs(urlparse(callback_url.strip()).query)
        error = (query.get("error") or [None])[0]
        if error:
            raise ValueError(f"授权被拒绝：{error}")
        parsed_code = (query.get("code") or [None])[0] or parsed_code
        parsed_state = (query.get("state") or [None])[0] or parsed_state
    if not parsed_code or not parsed_state:
        raise ValueError("请粘贴授权后地址栏里完整的 127.0.0.1 回调链接")
    return parsed_code, parsed_state


def looks_like_api_key(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith("xai-") or lowered.startswith("xai_") or lowered.startswith("sk-")


def latest_oauth_state(db: Session, account_id: int) -> OAuthState | None:
    return db.scalar(
        select(OAuthState)
        .where(OAuthState.account_id == account_id, OAuthState.expires_at > datetime.utcnow())
        .order_by(OAuthState.id.desc())
    )


def store_account_api_key(db: Session, account_id: int, api_key: str) -> UpstreamAccount:
    account = db.get(UpstreamAccount, account_id)
    if account is None:
        raise ValueError("授权对应的账号不存在")
    account.api_key_encrypted = encrypt_secret(api_key.strip(), get_settings().app_secret_key)
    if account.oauth_token is not None:
        db.delete(account.oauth_token)
        account.oauth_token = None
    return account


async def complete_oauth_from_paste(
    db: Session,
    *,
    account_id: int | None,
    pasted: str,
    code: str | None = None,
    state: str | None = None,
) -> UpstreamAccount:
    text = pasted.strip()
    if not text and not code:
        raise ValueError("请粘贴回调链接、页面上的授权码或 API Key")
    if "accounts.x.ai/oauth2/consent" in text and not parse_qs(urlparse(text).query).get("code"):
        raise ValueError("这是授权页地址，请粘贴页面上显示的代码或 API Key，不要粘这个网址")
    if text.startswith("http://") or text.startswith("https://"):
        parsed_code, parsed_state = parse_oauth_callback(text, code, state)
        if account_id is not None:
            record = db.scalar(select(OAuthState).where(OAuthState.state == parsed_state))
            if record is not None and record.account_id != account_id:
                raise ValueError("回调链接不属于当前账号")
        return await exchange_code(db, parsed_code, parsed_state)
    if looks_like_api_key(text):
        if account_id is None:
            raise ValueError("缺少账号")
        return store_account_api_key(db, account_id, text)
    if code and state:
        return await exchange_code(db, code, state)
    if account_id is None:
        raise ValueError("缺少账号")
    record = latest_oauth_state(db, account_id)
    if record is None:
        raise ValueError("没有进行中的授权，请重新点「去授权」")
    return await exchange_code(db, text or (code or ""), record.state)


def build_authorize_url(db: Session, account: UpstreamAccount) -> str:
    settings = get_settings()
    cleanup_expired_oauth_states(db)
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    db.add(
        OAuthState(
            state=state,
            code_verifier=verifier,
            account_id=account.id,
            expires_at=datetime.utcnow() + timedelta(minutes=15),
        )
    )
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.xai_oauth_client_id,
            "redirect_uri": settings.xai_oauth_redirect_uri,
            "scope": settings.xai_oauth_scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{settings.xai_oauth_authorize_url}?{query}"


async def exchange_code(db: Session, code: str, state: str) -> UpstreamAccount:
    settings = get_settings()
    record = db.scalar(select(OAuthState).where(OAuthState.state == state))
    if record is None or record.expires_at < datetime.utcnow():
        raise ValueError("授权状态无效或已过期")
    account = db.get(UpstreamAccount, record.account_id)
    if account is None:
        raise ValueError("授权对应的账号不存在")
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        response = await client.post(
            settings.xai_oauth_token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.xai_oauth_redirect_uri,
                "client_id": settings.xai_oauth_client_id,
                "code_verifier": record.code_verifier,
            },
            headers={"Accept": "application/json"},
        )
    db.delete(record)
    if response.status_code >= 400:
        raise ValueError(f"兑换 token 失败: {response.status_code} {response.text[:300]}")
    payload = response.json()
    _store_tokens(db, account, payload)
    return account


async def refresh_oauth_token(db: Session, account: UpstreamAccount) -> str:
    settings = get_settings()
    token = account.oauth_token
    if token is None or not token.refresh_token_encrypted:
        raise ValueError("Grok 授权已过期，请重新授权")
    refresh_token = decrypt_secret(token.refresh_token_encrypted, settings.app_secret_key)
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        response = await client.post(
            settings.xai_oauth_token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.xai_oauth_client_id,
            },
            headers={"Accept": "application/json"},
        )
    if response.status_code >= 400:
        raise ValueError("Grok token 刷新失败，请重新授权")
    _store_tokens(db, account, response.json())
    db.flush()
    return decrypt_secret(account.oauth_token.access_token_encrypted, settings.app_secret_key)  # type: ignore[union-attr]


async def refresh_if_needed(db: Session, account: UpstreamAccount) -> str:
    settings = get_settings()
    token = account.oauth_token
    if token is None:
        raise ValueError("Grok 账号尚未授权")
    if token.expires_at is None or token.expires_at > datetime.utcnow() + timedelta(seconds=60):
        return decrypt_secret(token.access_token_encrypted, settings.app_secret_key)
    return await refresh_oauth_token(db, account)


def accounts_due_for_oauth_refresh(
    db: Session,
    *,
    now: datetime | None = None,
    soon_seconds: int = OAUTH_REFRESH_SOON_SECONDS,
) -> list[UpstreamAccount]:
    cutoff = (now or datetime.utcnow()) + timedelta(seconds=soon_seconds)
    rows = db.scalars(
        select(UpstreamAccount)
        .options(joinedload(UpstreamAccount.oauth_token))
        .where(UpstreamAccount.status == "active")
    ).all()
    due: list[UpstreamAccount] = []
    for account in rows:
        token = account.oauth_token
        if token is None or not token.refresh_token_encrypted:
            continue
        if token.expires_at is not None and token.expires_at <= cutoff:
            due.append(account)
    return due


async def refresh_expiring_oauth_tokens() -> int:
    session = get_session_factory()()
    try:
        account_ids = [account.id for account in accounts_due_for_oauth_refresh(session)]
    finally:
        session.close()
    refreshed = 0
    for account_id in account_ids:
        session = get_session_factory()()
        try:
            account = session.scalar(
                select(UpstreamAccount)
                .options(joinedload(UpstreamAccount.oauth_token))
                .where(UpstreamAccount.id == account_id)
            )
            if account is None:
                continue
            await refresh_oauth_token(session, account)
            session.commit()
            refreshed += 1
        except Exception:
            session.rollback()
        finally:
            session.close()
    return refreshed


async def run_oauth_refresh_loop() -> None:
    while True:
        try:
            await refresh_expiring_oauth_tokens()
        except Exception:
            pass
        await asyncio.sleep(OAUTH_REFRESH_INTERVAL_SECONDS)


def _store_tokens(db: Session, account: UpstreamAccount, payload: dict) -> None:
    settings = get_settings()
    access = payload.get("access_token")
    if not access:
        raise ValueError("上游未返回 access_token")
    expires_in = int(payload.get("expires_in") or 3600)
    refresh = payload.get("refresh_token")
    if account.oauth_token is None:
        account.oauth_token = OAuthToken(account_id=account.id, access_token_encrypted="")
        db.add(account.oauth_token)
    account.oauth_token.access_token_encrypted = encrypt_secret(access, settings.app_secret_key)
    if refresh:
        account.oauth_token.refresh_token_encrypted = encrypt_secret(refresh, settings.app_secret_key)
    account.oauth_token.expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
    account.oauth_token.scope = payload.get("scope")
    account.oauth_token.updated_at = datetime.utcnow()

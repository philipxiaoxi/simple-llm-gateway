from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import decrypt_secret, encrypt_secret
from app.db import get_session_factory
from app.models import OAuthState, OAuthToken, UpstreamAccount

_listener_lock = threading.Lock()
_listener_started = False


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def cleanup_expired_oauth_states(db: Session) -> int:
    result = db.execute(delete(OAuthState).where(OAuthState.expires_at < datetime.utcnow()))
    return result.rowcount or 0


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
    ensure_loopback_listener()
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


async def refresh_if_needed(db: Session, account: UpstreamAccount) -> str:
    settings = get_settings()
    token = account.oauth_token
    if token is None:
        raise ValueError("Grok 账号尚未授权")
    if token.expires_at is None or token.expires_at > datetime.utcnow() + timedelta(seconds=60):
        return decrypt_secret(token.access_token_encrypted, settings.app_secret_key)
    if not token.refresh_token_encrypted:
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
    db.refresh(token)
    return decrypt_secret(account.oauth_token.access_token_encrypted, settings.app_secret_key)  # type: ignore[union-attr]


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


def ensure_loopback_listener() -> None:
    global _listener_started
    with _listener_lock:
        if _listener_started:
            return
        parsed = urlparse(get_settings().xai_oauth_redirect_uri)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 56121
        try:
            server = HTTPServer((host, port), _LoopbackHandler)
        except OSError:
            _listener_started = True
            return
        thread = threading.Thread(target=server.serve_forever, name="grok-oauth-loopback", daemon=True)
        thread.start()
        _listener_started = True


class _LoopbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != "/callback":
            self.send_error(404)
            return
        query = parse_qs(parsed.query)
        code = (query.get("code") or [None])[0]
        state = (query.get("state") or [None])[0]
        error = (query.get("error") or [None])[0]
        frontend = get_settings().app_base_url.rstrip("/")
        if error or not code or not state:
            location = f"{frontend}/accounts?oauth=error&reason={error or 'missing'}"
            self._redirect(location)
            return
        session = get_session_factory()()
        try:
            asyncio.run(exchange_code(session, code, state))
            session.commit()
            location = f"{frontend}/accounts?oauth=ok"
        except Exception:
            session.rollback()
            location = f"{frontend}/accounts?oauth=error&reason=exchange"
        finally:
            session.close()
        self._redirect(location)

    def _redirect(self, location: str) -> None:
        body = f'<html><body>授权完成，<a href="{location}">返回中转台</a></body></html>'.encode("utf-8")
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return

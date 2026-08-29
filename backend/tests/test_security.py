from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy import create_engine, text

from app.config import reset_settings, validate_app_secret_key, validate_bootstrap_admin_password
from app.db import get_session_factory, init_db, migrate_legacy_api_key_accounts, reset_db_runtime
from app.models import OAuthState
from app.seed import seed_admin
from app.services.grok_oauth import cleanup_expired_oauth_states


def test_validate_app_secret_key_rejects_examples() -> None:
    with pytest.raises(RuntimeError, match="APP_SECRET_KEY"):
        validate_app_secret_key("dev-only-change-me")
    with pytest.raises(RuntimeError, match="APP_SECRET_KEY"):
        validate_app_secret_key("change-me-to-a-long-random-string")
    with pytest.raises(RuntimeError, match="APP_SECRET_KEY"):
        validate_app_secret_key("short")
    validate_app_secret_key("unit-test-secret-key")


def test_validate_bootstrap_admin_password_rejects_examples() -> None:
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        validate_bootstrap_admin_password("changeme")
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        validate_bootstrap_admin_password("short")
    validate_bootstrap_admin_password("admin123")


def test_seed_rejects_example_password(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", "unit-test-secret-key")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "changeme")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "gateway.db"))
    reset_settings()
    reset_db_runtime()
    init_db()
    try:
        with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
            seed_admin()
    finally:
        reset_db_runtime()
        reset_settings()


def test_legacy_log_migration_preserves_log_metadata(tmp_path) -> None:
    from app.db import _migrate_legacy_logs

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE request_logs ("
                "id INTEGER PRIMARY KEY, request_body TEXT, response_body TEXT, status TEXT, http_status INTEGER)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO request_logs (id, request_body, response_body, status, http_status) "
                "VALUES (1, '{\"prompt\":\"secret\"}', '{\"answer\":\"ok\"}', 'success', 200)"
            )
        )

    _migrate_legacy_logs(engine)

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT id, request_body, response_body, status, http_status FROM request_logs")
        ).one()
    assert row == (1, None, None, "success", 200)


def test_legacy_api_key_migration_backfills_one_account(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-key.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE upstream_accounts (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"))
        connection.execute(
            text(
                "CREATE TABLE api_keys (id INTEGER PRIMARY KEY, name TEXT NOT NULL, account_id INTEGER)"
            )
        )
        connection.execute(text("INSERT INTO upstream_accounts (id, name) VALUES (9, '旧账号')"))
        connection.execute(text("INSERT INTO api_keys (id, name, account_id) VALUES (1, '老 Key', 9)"))
        connection.execute(
            text(
                "CREATE TABLE api_key_accounts ("
                "id INTEGER PRIMARY KEY, api_key_id INTEGER NOT NULL, account_id INTEGER NOT NULL, "
                "sort_order INTEGER NOT NULL, UNIQUE (api_key_id, account_id))"
            )
        )

        migrated = migrate_legacy_api_key_accounts(connection)
        repeated = migrate_legacy_api_key_accounts(connection)
        rows = connection.execute(
            text("SELECT api_key_id, account_id, sort_order FROM api_key_accounts")
        ).all()

    assert migrated == [{"key_id": 1, "key_name": "老 Key", "account_id": 9, "account_name": "旧账号"}]
    assert repeated == []
    assert rows == [(1, 9, 0)]


def test_cleanup_expired_oauth_states(client: TestClient, auth_headers: dict[str, str]) -> None:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Grok", "provider": "grok"},
    ).json()
    session = get_session_factory()()
    try:
        session.add(
            OAuthState(
                state="expired-state",
                code_verifier="verifier-expired",
                account_id=account["id"],
                expires_at=datetime.utcnow() - timedelta(minutes=1),
            )
        )
        session.add(
            OAuthState(
                state="fresh-state",
                code_verifier="verifier-fresh",
                account_id=account["id"],
                expires_at=datetime.utcnow() + timedelta(minutes=10),
            )
        )
        session.commit()
        cleanup_expired_oauth_states(session)
        session.commit()
        states = set(session.scalars(select(OAuthState.state)).all())
        assert "expired-state" not in states
        assert "fresh-state" in states
    finally:
        session.close()

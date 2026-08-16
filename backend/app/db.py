from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(database_api_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = database_api_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    return _SessionLocal


def init_db() -> None:
    engine = get_engine()
    _migrate_legacy_logs(engine)
    Base.metadata.create_all(bind=engine)
    _ensure_columns(engine)
    _ensure_request_logs_have_no_parent_fks(engine)


def _migrate_legacy_logs(engine: Engine) -> None:
    """旧版本把消息存在 request_logs.request_body，新版本拆到 request_log_messages。

    仅当 request_log_messages 表尚不存在（旧库首次升级）时清理一次遗留数据，
    避免每次启动都可能在消息表为空时清空全部日志。
    """
    with engine.begin() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        if "request_log_messages" in tables or "request_logs" not in tables:
            return
        has_old_bodies = connection.execute(
            text("SELECT 1 FROM request_logs WHERE request_body IS NOT NULL LIMIT 1")
        ).first() is not None
        if has_old_bodies:
            connection.execute(text("DELETE FROM request_logs"))


def _ensure_columns(engine: Engine) -> None:
    account_statements = {
        "models_json": "ALTER TABLE upstream_accounts ADD COLUMN models_json TEXT",
        "models_updated_at": "ALTER TABLE upstream_accounts ADD COLUMN models_updated_at DATETIME",
    }
    with engine.begin() as connection:
        account_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(upstream_accounts)"))
        }
        for column, statement in account_statements.items():
            if column not in account_columns:
                connection.execute(text(statement))
        log_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(request_logs)"))}
        if "updated_at" not in log_columns:
            connection.execute(text("ALTER TABLE request_logs ADD COLUMN updated_at DATETIME"))
        if "session_key" not in log_columns:
            connection.execute(text("ALTER TABLE request_logs ADD COLUMN session_key VARCHAR(128)"))
        if "reasoning_json" not in log_columns:
            connection.execute(text("ALTER TABLE request_logs ADD COLUMN reasoning_json TEXT"))
        admin_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(admins)"))}
        if "token_version" not in admin_columns:
            connection.execute(text("ALTER TABLE admins ADD COLUMN token_version INTEGER DEFAULT 0 NOT NULL"))
        log_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(request_logs)"))}
        if log_columns and "api_key_name" not in log_columns:
            connection.execute(text("ALTER TABLE request_logs ADD COLUMN api_key_name VARCHAR(128)"))
            connection.execute(
                text(
                    "UPDATE request_logs SET api_key_name = "
                    "(SELECT name FROM api_keys WHERE api_keys.id = request_logs.api_key_id) "
                    "WHERE api_key_name IS NULL"
                )
            )
        if log_columns and "account_name" not in log_columns:
            connection.execute(text("ALTER TABLE request_logs ADD COLUMN account_name VARCHAR(128)"))
            connection.execute(
                text(
                    "UPDATE request_logs SET account_name = "
                    "(SELECT name FROM upstream_accounts WHERE upstream_accounts.id = request_logs.account_id) "
                    "WHERE account_name IS NULL"
                )
            )


def _request_logs_have_parent_fks(engine: Engine) -> bool:
    with engine.connect() as connection:
        fks = list(connection.execute(text("PRAGMA foreign_key_list(request_logs)")))
        return any(row[2] in {"api_keys", "upstream_accounts"} for row in fks)


def _ensure_request_logs_have_no_parent_fks(engine: Engine) -> None:
    if not _request_logs_have_parent_fks(engine):
        return
    raw_connection = engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("BEGIN")
        cursor.execute(
            """
            CREATE TABLE request_logs_new (
                id INTEGER NOT NULL PRIMARY KEY,
                account_id INTEGER NOT NULL,
                account_name VARCHAR(128),
                api_key_id INTEGER,
                api_key_name VARCHAR(128),
                protocol VARCHAR(32) NOT NULL,
                model VARCHAR(128),
                stream BOOLEAN NOT NULL,
                status VARCHAR(16) NOT NULL,
                http_status INTEGER NOT NULL,
                error_message TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                latency_ms INTEGER NOT NULL,
                request_body TEXT,
                response_body TEXT,
                session_key VARCHAR(128),
                reasoning_json TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO request_logs_new (
                id, account_id, account_name, api_key_id, api_key_name, protocol, model, stream, status,
                http_status, error_message, prompt_tokens, completion_tokens, total_tokens,
                latency_ms, request_body, response_body, session_key, reasoning_json,
                created_at, updated_at
            )
            SELECT
                request_logs.id,
                request_logs.account_id,
                COALESCE(request_logs.account_name, upstream_accounts.name),
                request_logs.api_key_id,
                COALESCE(request_logs.api_key_name, api_keys.name),
                request_logs.protocol,
                request_logs.model,
                request_logs.stream,
                request_logs.status,
                request_logs.http_status,
                request_logs.error_message,
                request_logs.prompt_tokens,
                request_logs.completion_tokens,
                request_logs.total_tokens,
                request_logs.latency_ms,
                request_logs.request_body,
                request_logs.response_body,
                request_logs.session_key,
                request_logs.reasoning_json,
                request_logs.created_at,
                request_logs.updated_at
            FROM request_logs
            LEFT JOIN api_keys ON api_keys.id = request_logs.api_key_id
            LEFT JOIN upstream_accounts ON upstream_accounts.id = request_logs.account_id
            """
        )
        cursor.execute("DROP TABLE request_logs")
        cursor.execute("ALTER TABLE request_logs_new RENAME TO request_logs")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_request_logs_account_id ON request_logs (account_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_request_logs_api_key_id ON request_logs (api_key_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_request_logs_created_at ON request_logs (created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_request_logs_session_key ON request_logs (session_key)")
        cursor.execute("COMMIT")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception:
        try:
            raw_connection.rollback()
        except Exception:
            pass
        raise
    finally:
        raw_connection.close()


def reset_db_runtime() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

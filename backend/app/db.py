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
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-65536")
            cursor.execute("PRAGMA mmap_size=268435456")
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
    _ensure_api_keys_account_id_nullable(engine)
    _ensure_request_logs_have_no_parent_fks(engine)


def _migrate_legacy_logs(engine: Engine) -> None:
    """旧版本把消息存在 request_logs.request_body，新版本拆到 request_log_messages。

    仅当 request_log_messages 表尚不存在（旧库首次升级）时清除已被新表替代的正文，
    保留可用于审计和统计的历史日志元数据。
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
            text("SELECT 1 FROM request_logs WHERE request_body IS NOT NULL OR response_body IS NOT NULL LIMIT 1")
        ).first() is not None
        if has_old_bodies:
            connection.execute(text("UPDATE request_logs SET request_body = NULL, response_body = NULL"))


def _ensure_columns(engine: Engine) -> None:
    account_statements = {
        "source": "ALTER TABLE upstream_accounts ADD COLUMN source VARCHAR(16) DEFAULT 'upstream' NOT NULL",
        "agent_route_id": "ALTER TABLE upstream_accounts ADD COLUMN agent_route_id VARCHAR(128)",
        "models_json": "ALTER TABLE upstream_accounts ADD COLUMN models_json TEXT",
        "models_updated_at": "ALTER TABLE upstream_accounts ADD COLUMN models_updated_at DATETIME",
        "risk_level": "ALTER TABLE upstream_accounts ADD COLUMN risk_level VARCHAR(16) DEFAULT 'low' NOT NULL",
        "website_url": "ALTER TABLE upstream_accounts ADD COLUMN website_url VARCHAR(512)",
        "model_prefix": "ALTER TABLE upstream_accounts ADD COLUMN model_prefix VARCHAR(32)",
    }
    skill_settings_statements = {
        "report_account_id": "ALTER TABLE skill_classification_settings ADD COLUMN report_account_id INTEGER",
        "report_model": "ALTER TABLE skill_classification_settings ADD COLUMN report_model VARCHAR(128)",
        "report_enabled": "ALTER TABLE skill_classification_settings ADD COLUMN report_enabled BOOLEAN DEFAULT 0 NOT NULL",
    }
    skill_statements = {
        "analysis_json": "ALTER TABLE skills ADD COLUMN analysis_json TEXT",
        "analysis_generated_at": "ALTER TABLE skills ADD COLUMN analysis_generated_at DATETIME",
    }
    with engine.begin() as connection:
        account_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(upstream_accounts)"))
        }
        for column, statement in account_statements.items():
            if column not in account_columns:
                connection.execute(text(statement))
        settings_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(skill_classification_settings)"))
        }
        for column, statement in skill_settings_statements.items():
            if column not in settings_columns:
                connection.execute(text(statement))
        skill_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(skills)"))}
        for column, statement in skill_statements.items():
            if column not in skill_columns:
                connection.execute(text(statement))
        connection.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_upstream_accounts_agent_route_id ON upstream_accounts (agent_route_id)")
        )
        connection.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_gateway_agents_agent_id ON gateway_agents (agent_id)")
        )
        connection.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_gateway_agent_routes_route_id ON gateway_agent_routes (route_id)")
        )
        log_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(request_logs)"))}
        if "updated_at" not in log_columns:
            connection.execute(text("ALTER TABLE request_logs ADD COLUMN updated_at DATETIME"))
        if "session_key" not in log_columns:
            connection.execute(text("ALTER TABLE request_logs ADD COLUMN session_key VARCHAR(128)"))
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_request_logs_updated_at ON request_logs (updated_at)")
        )
        connection.execute(
            text("UPDATE request_logs SET updated_at = created_at WHERE updated_at IS NULL")
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_content_audit_findings_api_key_id "
                "ON content_audit_findings (api_key_id)"
            )
        )
        if "reasoning_json" not in log_columns:
            connection.execute(text("ALTER TABLE request_logs ADD COLUMN reasoning_json TEXT"))
        if "account_source" not in log_columns:
            connection.execute(text("ALTER TABLE request_logs ADD COLUMN account_source VARCHAR(16) DEFAULT 'upstream' NOT NULL"))
            connection.execute(
                text(
                    "UPDATE request_logs SET account_source = COALESCE("
                    "(SELECT source FROM upstream_accounts WHERE upstream_accounts.id = request_logs.account_id), "
                    "'upstream')"
                )
            )
        benchmark_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(benchmark_results)"))}
        if benchmark_columns and "timeout" not in benchmark_columns:
            connection.execute(text("ALTER TABLE benchmark_results ADD COLUMN timeout BOOLEAN DEFAULT 0 NOT NULL"))
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
        skill_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(skills)"))}
        if skill_columns and "category" in skill_columns:
            connection.execute(text("UPDATE skills SET category = substr(category, 1, 64) WHERE length(category) > 64"))
        _ensure_api_key_accounts(connection)
        _backfill_account_model_prefixes(connection)


def _ensure_api_key_accounts(connection) -> None:  # type: ignore[no-untyped-def]
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS api_key_accounts (
                id INTEGER NOT NULL PRIMARY KEY,
                api_key_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL,
                sort_order INTEGER NOT NULL,
                FOREIGN KEY(api_key_id) REFERENCES api_keys (id) ON DELETE CASCADE,
                FOREIGN KEY(account_id) REFERENCES upstream_accounts (id),
                UNIQUE (api_key_id, account_id)
            )
            """
        )
    )
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_api_key_accounts_api_key_id ON api_key_accounts (api_key_id)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_api_key_accounts_account_id ON api_key_accounts (account_id)"))
    migrate_legacy_api_key_accounts(connection)


def migrate_legacy_api_key_accounts(connection) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    """将旧 api_keys.account_id 转为有序的 api_key_accounts 关联。"""
    tables = {
        row[0]
        for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
    }
    if "api_keys" not in tables:
        return []
    pending = connection.execute(
        text(
            """
            SELECT api_keys.id AS key_id, api_keys.name AS key_name,
                   api_keys.account_id AS account_id, upstream_accounts.name AS account_name
            FROM api_keys
            LEFT JOIN upstream_accounts ON upstream_accounts.id = api_keys.account_id
            WHERE api_keys.account_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM api_key_accounts
                  WHERE api_key_accounts.api_key_id = api_keys.id
                    AND api_key_accounts.account_id = api_keys.account_id
              )
            """
        )
    ).mappings().all()
    connection.execute(
        text(
            """
            INSERT INTO api_key_accounts (api_key_id, account_id, sort_order)
            SELECT api_keys.id, api_keys.account_id, 0
            FROM api_keys
            WHERE api_keys.account_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM api_key_accounts
                  WHERE api_key_accounts.api_key_id = api_keys.id
                    AND api_key_accounts.account_id = api_keys.account_id
              )
            """
        )
    )
    return [dict(row) for row in pending]


def _backfill_account_model_prefixes(connection) -> None:  # type: ignore[no-untyped-def]
    from app.services.key_models import default_model_prefix

    tables = {
        row[0]
        for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
    }
    if "upstream_accounts" not in tables:
        return
    rows = connection.execute(text("SELECT id, name, model_prefix FROM upstream_accounts")).all()
    for account_id, name, prefix in rows:
        if str(prefix or "").strip():
            continue
        connection.execute(
            text("UPDATE upstream_accounts SET model_prefix = :prefix WHERE id = :id"),
            {"prefix": default_model_prefix(str(name or ""), int(account_id)), "id": account_id},
        )


def _api_keys_account_id_not_null(engine: Engine) -> bool:
    with engine.connect() as connection:
        columns = list(connection.execute(text("PRAGMA table_info(api_keys)")))
        for row in columns:
            if row[1] == "account_id":
                return bool(row[3])
    return False


def _ensure_api_keys_account_id_nullable(engine: Engine) -> None:
    if not _api_keys_account_id_not_null(engine):
        return
    raw_connection = engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("BEGIN")
        cursor.execute(
            """
            CREATE TABLE api_keys_new (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                key_hash VARCHAR(64) NOT NULL,
                key_encrypted TEXT NOT NULL,
                key_prefix VARCHAR(32) NOT NULL,
                account_id INTEGER,
                status VARCHAR(16) NOT NULL,
                created_at DATETIME NOT NULL,
                last_used_at DATETIME,
                FOREIGN KEY(account_id) REFERENCES upstream_accounts (id),
                UNIQUE (key_hash)
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO api_keys_new (
                id, name, key_hash, key_encrypted, key_prefix, account_id, status, created_at, last_used_at
            )
            SELECT
                id, name, key_hash, key_encrypted, key_prefix, account_id, status, created_at, last_used_at
            FROM api_keys
            """
        )
        cursor.execute("DROP TABLE api_keys")
        cursor.execute("ALTER TABLE api_keys_new RENAME TO api_keys")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_api_keys_key_hash ON api_keys (key_hash)")
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
                account_source VARCHAR(16) NOT NULL,
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
                id, account_id, account_name, account_source, api_key_id, api_key_name, protocol, model, stream, status,
                http_status, error_message, prompt_tokens, completion_tokens, total_tokens,
                latency_ms, request_body, response_body, session_key, reasoning_json,
                created_at, updated_at
            )
            SELECT
                request_logs.id,
                request_logs.account_id,
                COALESCE(request_logs.account_name, upstream_accounts.name),
                COALESCE(request_logs.account_source, upstream_accounts.source, 'upstream'),
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

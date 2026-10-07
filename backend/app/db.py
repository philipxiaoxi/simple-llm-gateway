from __future__ import annotations

import contextlib
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
    _migrate_douyin_settings_to_tikhub(engine)
    _migrate_info_poll_interval(engine)
    _ensure_api_keys_account_id_nullable(engine)
    _ensure_info_items_source_nullable(engine)
    _ensure_request_logs_have_no_parent_fks(engine)
    _ensure_knowledge_fts(engine)


def _ensure_knowledge_fts(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
                    text,
                    chunk_id UNINDEXED,
                    kb_id UNINDEXED,
                    document_id UNINDEXED,
                    source_name UNINDEXED
                )
                """
            )
        )
        kb_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(knowledge_bases)"))}
        if kb_columns:
            if "embedding_account_id" not in kb_columns:
                connection.execute(text("ALTER TABLE knowledge_bases ADD COLUMN embedding_account_id INTEGER"))
            if "embedding_model" not in kb_columns:
                connection.execute(text("ALTER TABLE knowledge_bases ADD COLUMN embedding_model VARCHAR(256)"))
            if "embedding_dimensions" not in kb_columns:
                connection.execute(text("ALTER TABLE knowledge_bases ADD COLUMN embedding_dimensions INTEGER"))
            if "scope" not in kb_columns:
                connection.execute(
                    text("ALTER TABLE knowledge_bases ADD COLUMN scope VARCHAR(16) DEFAULT 'public' NOT NULL")
                )
        doc_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(knowledge_documents)"))}
        if doc_columns:
            if "content_hash" not in doc_columns:
                connection.execute(text("ALTER TABLE knowledge_documents ADD COLUMN content_hash VARCHAR(64)"))


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
        "header_spoof": "ALTER TABLE upstream_accounts ADD COLUMN header_spoof VARCHAR(16) DEFAULT 'none' NOT NULL",
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
        added_header_spoof = "header_spoof" not in account_columns
        for column, statement in account_statements.items():
            if column not in account_columns:
                connection.execute(text(statement))
        if added_header_spoof:
            connection.execute(
                text("UPDATE upstream_accounts SET header_spoof = 'grok' WHERE provider = 'grok'")
            )
            connection.execute(
                text(
                    "UPDATE upstream_accounts SET header_spoof = 'opencode' WHERE provider = 'opencode_go'"
                )
            )
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
        # 列表与统计的真实筛选/排序组合；缺少这些索引时 status 过滤会退化成全表扫描
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_request_logs_status_created_at ON request_logs (status, created_at)")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_request_logs_model ON request_logs (model)")
        )
        # /api/admin/keys 的用量汇总按 api_key_id 过滤后要读 created_at 与 total_tokens
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_request_logs_api_key_usage "
                "ON request_logs (api_key_id, created_at, total_tokens)"
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_skills_updated_at ON skills (updated_at, id)")
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_content_audit_findings_category "
                "ON content_audit_findings (category)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_content_audit_findings_severity "
                "ON content_audit_findings (severity)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_benchmark_results_speed "
                "ON benchmark_results (ok, output_tokens_per_second)"
            )
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
        _ensure_site_diagram_columns(connection)
        _ensure_douyin_columns(connection)
        _ensure_douyin_job_columns(connection)
        _ensure_info_item_ai_columns(connection)
        _ensure_info_public_session_columns(connection)
        _ensure_api_key_accounts(connection)
        _backfill_account_model_prefixes(connection)


def _ensure_site_diagram_columns(connection) -> None:  # type: ignore[no-untyped-def]
    """站点表增量列：origin 判别列与 diagram 版本元数据。"""
    site_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(sites)"))}
    if site_columns and "origin" not in site_columns:
        connection.execute(
            text("ALTER TABLE sites ADD COLUMN origin VARCHAR(16) DEFAULT 'upload' NOT NULL")
        )
    version_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(site_versions)"))}
    if version_columns:
        for column in ("diagram_type", "source_json", "quality"):
            if column not in version_columns:
                connection.execute(text(f"ALTER TABLE site_versions ADD COLUMN {column} TEXT"))
    if site_columns:
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_sites_origin_key_created ON sites (origin, mcp_key_id, created_at)")
        )


def _ensure_douyin_columns(connection) -> None:  # type: ignore[no-untyped-def]
    """douyin_settings 增量列：TikHub 托管解析 API 配置。"""
    columns = {row[1] for row in connection.execute(text("PRAGMA table_info(douyin_settings)"))}
    if not columns:
        return
    if "tikhub_base_url" not in columns:
        connection.execute(
            text("ALTER TABLE douyin_settings ADD COLUMN tikhub_base_url VARCHAR(256) DEFAULT '' NOT NULL")
        )
    if "tikhub_api_key_encrypted" not in columns:
        connection.execute(text("ALTER TABLE douyin_settings ADD COLUMN tikhub_api_key_encrypted TEXT"))
    if "tikhub_updated_at" not in columns:
        connection.execute(text("ALTER TABLE douyin_settings ADD COLUMN tikhub_updated_at DATETIME"))


def _migrate_douyin_settings_to_tikhub(engine: Engine) -> None:
    """把旧 douyin_settings 的 TikHub 凭据幂等迁移到共享 tikhub_settings。

    仅当 tikhub_settings 不存在任何记录时执行。清除凭据时保留单例空记录，
    因此清除后重启不会被旧数据复活。旧表数据保留供回滚。
    """
    with engine.begin() as connection:
        tables = {
            row[0]
            for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        if "douyin_settings" not in tables or "tikhub_settings" not in tables:
            return
        existing = connection.execute(text("SELECT 1 FROM tikhub_settings LIMIT 1")).first()
        if existing is not None:
            return
        legacy_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(douyin_settings)"))
        }
        if not {"tikhub_base_url", "tikhub_api_key_encrypted"} <= legacy_columns:
            return
        legacy = connection.execute(
            text(
                "SELECT tikhub_base_url, tikhub_api_key_encrypted, tikhub_updated_at "
                "FROM douyin_settings WHERE id = 1"
            )
        ).first()
        if legacy is None:
            return
        base_url = (legacy[0] or "").strip()
        encrypted = legacy[1]
        if not base_url and not encrypted:
            return
        connection.execute(
            text(
                "INSERT INTO tikhub_settings (id, base_url, api_key_encrypted, updated_at) "
                "VALUES (1, :base_url, :encrypted, :updated_at)"
            ),
            {"base_url": base_url, "encrypted": encrypted, "updated_at": legacy[2]},
        )


def _migrate_info_poll_interval(engine: Engine) -> None:
    """一次性把资讯信源的采集间隔统一为 24 小时（86400 秒）。

    用 app_migrations 记录是否执行过，避免每次启动覆盖用户后续的调整。
    """
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS app_migrations "
                "(key VARCHAR(64) PRIMARY KEY, applied_at DATETIME)"
            )
        )
        done = connection.execute(
            text("SELECT 1 FROM app_migrations WHERE key = :key"),
            {"key": "info_poll_interval_24h"},
        ).first()
        if done is not None:
            return
        tables = {
            row[0]
            for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        if "info_sources" in tables:
            connection.execute(
                text(
                    "UPDATE info_sources SET poll_interval_seconds = 86400 "
                    "WHERE poll_interval_seconds != 86400"
                )
            )
        connection.execute(
            text("INSERT INTO app_migrations (key, applied_at) VALUES (:key, datetime('now'))"),
            {"key": "info_poll_interval_24h"},
        )


def _ensure_douyin_job_columns(connection) -> None:  # type: ignore[no-untyped-def]
    """douyin_jobs 增量列：下载进度字节数。"""
    columns = {row[1] for row in connection.execute(text("PRAGMA table_info(douyin_jobs)"))}
    if not columns:
        return
    if "downloaded_bytes" not in columns:
        connection.execute(
            text("ALTER TABLE douyin_jobs ADD COLUMN downloaded_bytes INTEGER DEFAULT 0 NOT NULL")
        )
    if "expected_bytes" not in columns:
        connection.execute(
            text("ALTER TABLE douyin_jobs ADD COLUMN expected_bytes INTEGER DEFAULT 0 NOT NULL")
        )


def _ensure_info_item_ai_columns(connection) -> None:  # type: ignore[no-untyped-def]
    """info_items 增量列：AI 判定结果与精选标记。"""
    columns = {row[1] for row in connection.execute(text("PRAGMA table_info(info_items)"))}
    if not columns:
        return
    statements = {
        "ai_status": "ALTER TABLE info_items ADD COLUMN ai_status VARCHAR(16) DEFAULT 'pending' NOT NULL",
        "ai_label": "ALTER TABLE info_items ADD COLUMN ai_label VARCHAR(32) DEFAULT '' NOT NULL",
        "ai_score": "ALTER TABLE info_items ADD COLUMN ai_score INTEGER",
        "ai_reason": "ALTER TABLE info_items ADD COLUMN ai_reason TEXT DEFAULT '' NOT NULL",
        "ai_tags_json": "ALTER TABLE info_items ADD COLUMN ai_tags_json TEXT",
        "ai_model": "ALTER TABLE info_items ADD COLUMN ai_model VARCHAR(128) DEFAULT '' NOT NULL",
        "ai_error": "ALTER TABLE info_items ADD COLUMN ai_error TEXT DEFAULT '' NOT NULL",
        "ai_attempts": "ALTER TABLE info_items ADD COLUMN ai_attempts INTEGER DEFAULT 0 NOT NULL",
        "ai_scored_at": "ALTER TABLE info_items ADD COLUMN ai_scored_at DATETIME",
        "is_featured": "ALTER TABLE info_items ADD COLUMN is_featured BOOLEAN DEFAULT 0 NOT NULL",
        "ai_featured_manual": "ALTER TABLE info_items ADD COLUMN ai_featured_manual BOOLEAN DEFAULT 0 NOT NULL",
        "ai_hidden_manual": "ALTER TABLE info_items ADD COLUMN ai_hidden_manual BOOLEAN DEFAULT 0 NOT NULL",
        "content_status": "ALTER TABLE info_items ADD COLUMN content_status VARCHAR(16) DEFAULT '' NOT NULL",
        "content_attempts": "ALTER TABLE info_items ADD COLUMN content_attempts INTEGER DEFAULT 0 NOT NULL",
        "content_html": "ALTER TABLE info_items ADD COLUMN content_html TEXT",
    }
    for column, statement in statements.items():
        if column not in columns:
            connection.execute(text(statement))
    connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_info_items_ai_status ON info_items (ai_status)")
    )
    connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_info_items_is_featured ON info_items (is_featured)")
    )


def _ensure_info_public_session_columns(connection) -> None:  # type: ignore[no-untyped-def]
    """info_public_sessions 增量列：解锁口令指纹。"""
    columns = {row[1] for row in connection.execute(text("PRAGMA table_info(info_public_sessions)"))}
    if not columns:
        return
    if "password_fingerprint" not in columns:
        connection.execute(
            text(
                "ALTER TABLE info_public_sessions "
                "ADD COLUMN password_fingerprint VARCHAR(16) DEFAULT '' NOT NULL"
            )
        )


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


def _info_items_source_id_not_null(engine: Engine) -> bool:
    with engine.begin() as connection:
        columns = list(connection.execute(text("PRAGMA table_info(info_items)")))
    source = next((row for row in columns if row[1] == "source_id"), None)
    return bool(source and source[3])


def _ensure_info_items_source_nullable(engine: Engine) -> None:
    """把 info_items.source_id 改成可空（删渠道默认保留内容，DB 侧 SET NULL）。

    SQLite 不能直接改列约束，沿用 `_ensure_api_keys_account_id_nullable` 的重建表做法。
    只在检测到旧的 NOT NULL 结构时才执行，全新库由 create_all 直接建出正确结构。
    """
    if not _info_items_source_id_not_null(engine):
        return
    raw_connection = engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("BEGIN")
        cursor.execute(
            """
            CREATE TABLE info_items_new (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                source_id VARCHAR(36),
                external_id VARCHAR(64) NOT NULL,
                kind VARCHAR(16) NOT NULL,
                text TEXT NOT NULL,
                excerpt VARCHAR(512) NOT NULL,
                permalink VARCHAR(512) NOT NULL,
                author_name VARCHAR(128) NOT NULL,
                source_type VARCHAR(16) NOT NULL,
                published_at DATETIME,
                views INTEGER,
                views_text VARCHAR(16),
                reactions_total INTEGER,
                reactions_json TEXT,
                is_forwarded BOOLEAN NOT NULL,
                link_preview_json TEXT,
                media_count INTEGER NOT NULL,
                cover_media_id VARCHAR(36),
                cover_seed INTEGER NOT NULL,
                status VARCHAR(16) NOT NULL,
                is_favorite BOOLEAN NOT NULL,
                is_hidden BOOLEAN NOT NULL,
                collected_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(source_id) REFERENCES info_sources (id) ON DELETE SET NULL,
                UNIQUE (source_id, external_id)
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO info_items_new (
                id, source_id, external_id, kind, text, excerpt, permalink, author_name, source_type,
                published_at, views, views_text, reactions_total, reactions_json, is_forwarded,
                link_preview_json, media_count, cover_media_id, cover_seed, status, is_favorite,
                is_hidden, collected_at, created_at
            )
            SELECT
                id, source_id, external_id, kind, text, excerpt, permalink, author_name, source_type,
                published_at, views, views_text, reactions_total, reactions_json, is_forwarded,
                link_preview_json, media_count, cover_media_id, cover_seed, status, is_favorite,
                is_hidden, collected_at, created_at
            FROM info_items
            """
        )
        cursor.execute("DROP TABLE info_items")
        cursor.execute("ALTER TABLE info_items_new RENAME TO info_items")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_info_items_source_id ON info_items (source_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_info_items_kind ON info_items (kind)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_info_items_published_at ON info_items (published_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_info_items_is_favorite ON info_items (is_favorite)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_info_items_is_hidden ON info_items (is_hidden)")
        cursor.execute("COMMIT")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception:
        with contextlib.suppress(Exception):
            raw_connection.rollback()
        raise
    finally:
        raw_connection.close()


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

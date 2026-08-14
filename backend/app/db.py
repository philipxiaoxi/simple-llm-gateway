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
    Base.metadata.create_all(bind=engine)
    _ensure_columns(engine)


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

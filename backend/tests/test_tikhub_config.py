from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_engine, get_session_factory


def _seed_legacy_row(base_url: str = "https://api.tikhub.io", encrypted: str = "encrypted-blob") -> None:
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM tikhub_settings"))
        connection.execute(text("DELETE FROM douyin_settings WHERE id = 1"))
        connection.execute(
            text(
                "INSERT INTO douyin_settings "
                "(id, tikhub_base_url, tikhub_api_key_encrypted, tikhub_updated_at, updated_at) "
                "VALUES (1, :base_url, :encrypted, NULL, CURRENT_TIMESTAMP)"
            ),
            {"base_url": base_url, "encrypted": encrypted},
        )


def test_tikhub_endpoint_shares_with_legacy_and_encrypts(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    initial = client.get("/api/admin/integrations/tikhub", headers=auth_headers).json()
    assert initial["configured"] is False
    assert initial["shared_with_douyin"] is True

    saved = client.put(
        "/api/admin/integrations/tikhub",
        headers=auth_headers,
        json={"base_url": "https://api.tikhub.io", "api_key": "tk_secret"},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["configured"] is True
    assert body["has_key"] is True
    assert body["source"] == "page"
    assert "tk_secret" not in saved.text

    engine = get_engine()
    with engine.begin() as connection:
        encrypted = connection.execute(
            text("SELECT api_key_encrypted FROM tikhub_settings WHERE id = 1")
        ).scalar()
    assert encrypted and "tk_secret" not in encrypted

    legacy = client.get("/api/admin/mcp/douyin/provider", headers=auth_headers).json()
    assert legacy["configured"] is True

    cleared = client.delete("/api/admin/mcp/douyin/provider", headers=auth_headers).json()
    assert cleared["configured"] is False


def test_migration_is_idempotent(client: TestClient) -> None:
    from app.db import _migrate_douyin_settings_to_tikhub

    _seed_legacy_row()
    engine = get_engine()
    _migrate_douyin_settings_to_tikhub(engine)
    _migrate_douyin_settings_to_tikhub(engine)

    with engine.begin() as connection:
        rows = connection.execute(
            text("SELECT base_url, api_key_encrypted FROM tikhub_settings")
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "https://api.tikhub.io"
    assert rows[0][1] == "encrypted-blob"


def test_clear_survives_restart_migration(client: TestClient) -> None:
    from app.db import _migrate_douyin_settings_to_tikhub

    _seed_legacy_row()
    engine = get_engine()
    _migrate_douyin_settings_to_tikhub(engine)

    db = get_session_factory()()
    try:
        from app.services import tikhub_config

        tikhub_config.clear_tikhub_config(db)
        db.commit()
    finally:
        db.close()

    _migrate_douyin_settings_to_tikhub(engine)

    with engine.begin() as connection:
        row = connection.execute(
            text("SELECT base_url, api_key_encrypted FROM tikhub_settings")
        ).first()
    assert row is not None
    assert row[0] == ""
    assert row[1] is None

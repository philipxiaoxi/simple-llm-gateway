#!/usr/bin/env python3
"""迁移旧 API Key 的单账号绑定，并输出每条迁移明细。"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import get_engine, migrate_legacy_api_key_accounts  # noqa: E402


def main() -> None:
    engine = get_engine()
    with engine.begin() as connection:
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
        migrated = migrate_legacy_api_key_accounts(connection)

    print(f"数据库：{get_settings().database_path}")
    if not migrated:
        print("没有需要迁移的旧 API Key。")
        return
    print(f"已迁移 {len(migrated)} 个旧 API Key：")
    for item in migrated:
        account_name = item["account_name"] or "账号已不存在"
        print(f"  Key #{item['key_id']}「{item['key_name']}」 -> 账号 #{item['account_id']}「{account_name}」")
    print("说明：旧版本只保存一个 account_id，因此每个旧 Key 只能恢复这一条历史绑定；其它账号需要手动编辑 Key 添加。")


if __name__ == "__main__":
    main()
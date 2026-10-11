"""MCP Key 的能力专属配置读写。

每个 `McpKeyCapability` 行带一段 `config_json`，用于存放该 Key 在该能力下的专属设置
（例如「生成」能力用哪个模型）。这里提供通用的读写与序列化，任何能力都可以复用。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpKey, McpKeyCapability


def _parse(config_json: str | None) -> dict[str, Any]:
    try:
        data = json.loads(config_json or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def get_capability_config(mcp_key: McpKey | None, capability_id: str) -> dict[str, Any]:
    if mcp_key is None:
        return {}
    for item in mcp_key.capabilities:
        if item.capability_id == capability_id:
            return _parse(item.config_json)
    return {}


def all_capability_configs(mcp_key: McpKey | None) -> dict[str, dict[str, Any]]:
    if mcp_key is None:
        return {}
    return {item.capability_id: _parse(item.config_json) for item in mcp_key.capabilities}


def set_capability_config(
    db: Session, mcp_key_id: int, capability_id: str, config: dict[str, Any]
) -> McpKeyCapability:
    row = db.scalar(
        select(McpKeyCapability).where(
            McpKeyCapability.mcp_key_id == mcp_key_id,
            McpKeyCapability.capability_id == capability_id,
        )
    )
    if row is None:
        raise ValueError(f"该 Key 未授权能力 {capability_id}")
    row.config_json = json.dumps(config or {}, ensure_ascii=False)
    db.flush()
    return row

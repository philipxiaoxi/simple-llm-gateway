from __future__ import annotations

from fastapi import APIRouter, Depends

from app.capabilities import catalog_payload, ensure_defaults
from app.deps import get_current_admin

router = APIRouter(
    prefix="/api/admin/mcp/catalog",
    tags=["admin-mcp-catalog"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("")
def list_catalog():
    """广场服务目录与 MCP Key 可授权能力列表的唯一数据源。"""
    ensure_defaults()
    return {"items": catalog_payload(only_enabled=False)}

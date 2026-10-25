from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.capabilities import catalog_payload, ensure_defaults
from app.db import get_db
from app.deps import get_current_admin
from app.models import Admin
from app.services.permission_guard import has_permission, permissions_for_admin

router = APIRouter(
    prefix="/api/admin/mcp/catalog",
    tags=["admin-mcp-catalog"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("")
def list_catalog(
    scope: str = Query(default="plaza", pattern="^(plaza|all)$"),
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """广场服务目录与 MCP Key 可授权能力列表的唯一数据源。

    - scope=plaza（默认）：只返回当前账号按功能有权限的能力，用于 MCP 广场；
    - scope=all：返回全部能力，用于 MCP Key 能力授权与管理端筛选。
    """
    ensure_defaults()
    rows = catalog_payload(only_enabled=False)
    if scope == "plaza":
        permissions = permissions_for_admin(db, admin)
        rows = [
            row
            for row in rows
            if not row["permissions"]
            or any(has_permission(permissions, code) for code in row["permissions"])
        ]
    return {"items": rows}

from __future__ import annotations

from typing import Any

from app.capabilities.base import CallContext, CapabilitySpec, McpToolDef
from app.capabilities.site import sites as site_service

from . import renderer, service
from .errors import DiagramError


class DiagramProvider:
    spec = CapabilitySpec(
        capability_id="diagram",
        name="图表生成",
        description="提交 Archify 类型化 JSON 源，渲染为自包含交互式 HTML 并发布为可回滚、可令牌保护的图表站点",
        version="1.0.0",
        category="diagram",
        status="enabled",
        admin_path="/mcp-plaza/diagrams",
        icon="shapes",
        input_schema={
            "create": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "source": {"type": "object"},
                    "slug": {"type": "string"},
                    "name": {"type": "string"},
                    "quality": {"type": "string"},
                    "entry": {"type": "string"},
                    "activate": {"type": "boolean"},
                },
                "required": ["type", "source"],
            },
            "update": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "new_slug": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "access_mode": {"type": "string"},
                    "entry_file": {"type": "string"},
                    "spa_fallback": {"type": "boolean"},
                    "status": {"type": "string"},
                },
                "required": ["slug"],
            },
            "access": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "mode": {"type": "string"},
                    "reset_token": {"type": "boolean"},
                },
                "required": ["slug", "mode"],
            },
        },
        integration={
            "rest_endpoints": [
                {
                    "method": "POST",
                    "path": "/v1/diagrams",
                    "summary": "提交 JSON 源渲染并发布图表",
                    "content_type": "application/json",
                },
                {"method": "GET", "path": "/v1/diagrams", "summary": "列出本站点 Key 创建的图表"},
                {"method": "GET", "path": "/v1/diagrams/{slug}", "summary": "图表详情与版本"},
                {"method": "GET", "path": "/v1/diagrams/{slug}/source", "summary": "查看指定版本的 JSON 源"},
                {"method": "PATCH", "path": "/v1/diagrams/{slug}", "summary": "编辑图表元信息"},
                {"method": "POST", "path": "/v1/diagrams/{slug}/rollback", "summary": "回滚到指定版本"},
                {"method": "POST", "path": "/v1/diagrams/{slug}/access", "summary": "切换公开/令牌保护"},
                {"method": "GET", "path": "/v1/diagrams/{slug}/access", "summary": "查询访问模式与令牌"},
                {"method": "DELETE", "path": "/v1/diagrams/{slug}", "summary": "删除图表"},
            ],
            "notes": [
                "type 取值：architecture / workflow / sequence / dataflow / lifecycle",
                "source 为 Archify 类型化 JSON 源，传对象或 JSON 字符串均可",
                "source 未通过校验时返回 diagram_invalid，附带 diagnostics 规则码与 supportedFixes",
                "预览地址为 {origin}/sites/{slug}/；令牌模式下响应带 token 与拼好的 ?token= url",
                "编辑图表 = 用相同 slug 再次调用 diagram_create，追加新版本，可 rollback",
            ],
        },
    )

    def list_mcp_tools(self) -> list[McpToolDef]:
        return [
            McpToolDef(
                name="diagram_create",
                description=(
                    "提交 Archify 类型化 JSON 源，渲染为自包含交互式 HTML 并发布为图表站点。"
                    "type 为 architecture/workflow/sequence/dataflow/lifecycle；source 为 JSON 对象或字符串。"
                    "传 slug 更新已有图表（追加版本），不传则新建。返回站点、版本与预览地址。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "description": "图表类型，五选一"},
                        "source": {"type": "object", "description": "Archify 类型化 JSON 源"},
                        "slug": {"type": "string", "description": "目标图表 slug；为空则新建"},
                        "name": {"type": "string", "description": "新建图表时的显示名"},
                        "quality": {
                            "type": "string",
                            "description": "校验档位 showcase（默认）或 standard",
                        },
                        "activate": {"type": "boolean", "description": "是否设为当前版本，默认 true"},
                    },
                    "required": ["type", "source"],
                },
                operation="create",
            ),
            McpToolDef(
                name="diagram_list",
                description="列出当前 MCP Key 创建的图表",
                input_schema={"type": "object", "properties": {}},
                operation="list",
            ),
            McpToolDef(
                name="diagram_status",
                description="查看图表详情与版本列表",
                input_schema={
                    "type": "object",
                    "properties": {"slug": {"type": "string"}},
                    "required": ["slug"],
                },
                operation="status",
            ),
            McpToolDef(
                name="diagram_get_source",
                description="读取图表指定版本的 JSON 源，用于修改后重新创建。不传 version_no 时返回当前版本。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string"},
                        "version_no": {"type": "integer"},
                    },
                    "required": ["slug"],
                },
                operation="get_source",
            ),
            McpToolDef(
                name="diagram_update",
                description=(
                    "编辑图表元信息：显示名、描述、slug、入口文件、SPA 兜底、启用状态、访问模式。"
                    "只传需要改动的字段；未传的保持不变。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string", "description": "要编辑的图表 slug"},
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "new_slug": {"type": "string"},
                        "entry_file": {"type": "string"},
                        "spa_fallback": {"type": "boolean"},
                        "status": {"type": "string", "description": "active 或 disabled"},
                        "access_mode": {"type": "string", "description": "public 或 token"},
                    },
                    "required": ["slug"],
                },
                operation="update",
            ),
            McpToolDef(
                name="diagram_rollback",
                description="把图表当前版本切换为指定历史版本",
                input_schema={
                    "type": "object",
                    "properties": {"slug": {"type": "string"}, "version_no": {"type": "integer"}},
                    "required": ["slug", "version_no"],
                },
                operation="rollback",
            ),
            McpToolDef(
                name="diagram_access",
                description=(
                    "设置图表访问模式 public/token；token 模式下返回明文令牌和已拼好 ?token= 的可访问 url，"
                    "可直接发给用户。重置令牌时传 reset_token=true。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string"},
                        "mode": {"type": "string", "description": "public 或 token"},
                        "reset_token": {"type": "boolean"},
                    },
                    "required": ["slug", "mode"],
                },
                operation="access",
            ),
            McpToolDef(
                name="diagram_access_info",
                description="查询图表当前访问模式、明文令牌（令牌模式）与可直接访问的 url。",
                input_schema={
                    "type": "object",
                    "properties": {"slug": {"type": "string"}},
                    "required": ["slug"],
                },
                operation="access_info",
            ),
            McpToolDef(
                name="diagram_delete",
                description="删除图表及其全部版本",
                input_schema={
                    "type": "object",
                    "properties": {"slug": {"type": "string"}},
                    "required": ["slug"],
                },
                operation="delete",
            ),
        ]

    async def dispatch(self, operation: str, payload: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        if ctx.mcp_key is None:
            raise DiagramError("需要 MCP Key", status_code=401, error_type="authentication_error")
        key_id = ctx.mcp_key.id
        slug = str(payload.get("slug") or "").strip()

        if operation in {"create", "diagram_create"}:
            return await service.create_diagram(
                ctx.db,
                mcp_key_id=key_id,
                diagram_type=payload.get("type"),
                source=payload.get("source"),
                quality=payload.get("quality"),
                slug=(str(payload.get("slug")).strip() if payload.get("slug") else None),
                name=(str(payload.get("name")).strip() if payload.get("name") else None),
                activate=bool(payload.get("activate", True)),
            )
        if operation in {"list", "diagram_list"}:
            rows, _total = service.list_diagrams(ctx.db, mcp_key_id=key_id)
            items = [
                site_service.site_payload(site, current=site_service.current_version(ctx.db, site))
                for site in rows
            ]
            return {"items": items}
        if operation in {"status", "diagram_status"}:
            site = service.get_diagram(ctx.db, slug, key_id)
            return site_service.site_detail_payload(ctx.db, site)
        if operation in {"get_source", "diagram_get_source"}:
            site = service.get_diagram(ctx.db, slug, key_id)
            version_no = payload.get("version_no")
            return service.get_source(ctx.db, site, int(version_no) if version_no is not None else None)
        if operation in {"update", "diagram_update"}:
            site = service.get_diagram(ctx.db, slug, key_id)
            fields: dict[str, Any] = {}
            if "new_slug" in payload:
                fields["slug"] = payload["new_slug"]
            for name in ("name", "description", "entry_file", "spa_fallback", "status", "access_mode"):
                if name in payload:
                    fields[name] = payload[name]
            site_service.update_site(ctx.db, site, **fields)
            return site_service.site_detail_payload(ctx.db, site)
        if operation in {"rollback", "diagram_rollback"}:
            site = service.get_diagram(ctx.db, slug, key_id)
            version = site_service.rollback(ctx.db, site, int(payload.get("version_no") or 0))
            return {
                "site": site_service.site_payload(site, current=version),
                "version": site_service.version_payload(version, current_version_id=site.current_version_id),
            }
        if operation in {"access", "diagram_access"}:
            site = service.get_diagram(ctx.db, slug, key_id)
            site, token = site_service.set_access(
                ctx.db,
                site,
                mode=str(payload.get("mode") or ""),
                reset_token=bool(payload.get("reset_token")),
            )
            body = site_service.access_info(ctx.db, site)
            body["token"] = token
            body["url"] = site_service.access_url(site, token)
            return body
        if operation in {"access_info", "diagram_access_info"}:
            site = service.get_diagram(ctx.db, slug, key_id)
            return site_service.access_info(ctx.db, site)
        if operation in {"delete", "diagram_delete"}:
            site = service.get_diagram(ctx.db, slug, key_id)
            deleted_slug = site.slug
            site_service.delete_site(ctx.db, site)
            return {"deleted": True, "slug": deleted_slug}
        raise DiagramError(f"未知操作: {operation}", status_code=404, error_type="not_found")

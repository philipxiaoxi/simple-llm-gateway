from __future__ import annotations

import base64
import binascii
from typing import Any

from app.capabilities.base import CallContext, CapabilitySpec, McpToolDef
from app.config import get_settings

from . import sites as service
from .errors import SiteError


class SiteProvider:
    spec = CapabilitySpec(
        capability_id="site",
        name="站点部署",
        description="上传前端静态资源 zip，生成可回滚、可令牌保护的预览站点",
        version="1.0.0",
        category="hosting",
        status="enabled",
        admin_path="/mcp-plaza/sites",
        icon="globe",
        input_schema={
            "deploy": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "archive_base64": {"type": "string"},
                    "slug": {"type": "string"},
                    "name": {"type": "string"},
                    "entry": {"type": "string"},
                    "activate": {"type": "boolean"},
                },
                "required": ["filename", "archive_base64"],
            },
            "status": {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]},
            "rollback": {
                "type": "object",
                "properties": {"slug": {"type": "string"}, "version_no": {"type": "integer"}},
                "required": ["slug", "version_no"],
            },
            "delete": {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]},
            "access": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "mode": {"type": "string"},
                    "reset_token": {"type": "boolean"},
                },
                "required": ["slug", "mode"],
            },
            "access_info": {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]},
        },
        integration={
            "rest_endpoints": [
                {
                    "method": "POST",
                    "path": "/v1/sites",
                    "summary": "上传 zip 部署站点",
                    "content_type": "multipart/form-data",
                },
                {"method": "GET", "path": "/v1/sites", "summary": "列出本站点 Key 创建的站点"},
                {"method": "GET", "path": "/v1/sites/{slug}", "summary": "站点详情与版本"},
                {
                    "method": "GET",
                    "path": "/v1/sites/{slug}/versions/{version_no}",
                    "summary": "轮询版本状态与进度",
                },
                {"method": "POST", "path": "/v1/sites/{slug}/rollback", "summary": "回滚到指定版本"},
                {
                    "method": "POST",
                    "path": "/v1/sites/{slug}/access",
                    "summary": "切换公开/令牌保护；token 模式返回明文令牌与可访问 URL",
                },
                {
                    "method": "GET",
                    "path": "/v1/sites/{slug}/access",
                    "summary": "查询当前访问模式、令牌与可访问 URL",
                },
                {"method": "DELETE", "path": "/v1/sites/{slug}", "summary": "删除站点"},
            ],
            "notes": [
                "REST 部署为异步：先返回 unpacking，再轮询版本状态到 ready/duplicate/failed",
                "MCP site_deploy 的 archive_base64 解码后不超过 10MB，更大文件走 REST multipart",
                "预览地址为 {origin}/sites/{slug}/；令牌模式下响应会带 token 与已拼好 ?token= 的 url，可直接交给用户",
            ],
        },
    )

    def list_mcp_tools(self) -> list[McpToolDef]:
        limit = get_settings().site_mcp_max_bytes
        return [
            McpToolDef(
                name="site_deploy",
                description=(
                    f"上传前端静态资源 zip 并部署为预览站点。archive_base64 解码后不超过 {limit} 字节，"
                    "更大文件走 REST multipart。返回站点、版本与预览地址。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "归档文件名，需以 .zip 结尾"},
                        "archive_base64": {"type": "string", "description": "zip 内容的 base64"},
                        "slug": {"type": "string", "description": "目标站点 slug；为空则新建站点"},
                        "name": {"type": "string", "description": "新建站点时的显示名"},
                        "entry": {"type": "string", "description": "入口文件，默认 index.html"},
                        "activate": {"type": "boolean", "description": "是否设为当前版本，默认 true"},
                    },
                    "required": ["filename", "archive_base64"],
                },
                operation="deploy",
            ),
            McpToolDef(
                name="site_list",
                description="列出当前 MCP Key 创建的站点",
                input_schema={"type": "object", "properties": {}},
                operation="list",
            ),
            McpToolDef(
                name="site_status",
                description="查看站点详情与版本列表",
                input_schema={
                    "type": "object",
                    "properties": {"slug": {"type": "string"}},
                    "required": ["slug"],
                },
                operation="status",
            ),
            McpToolDef(
                name="site_rollback",
                description="把站点当前版本切换为指定历史版本",
                input_schema={
                    "type": "object",
                    "properties": {"slug": {"type": "string"}, "version_no": {"type": "integer"}},
                    "required": ["slug", "version_no"],
                },
                operation="rollback",
            ),
            McpToolDef(
                name="site_delete",
                description="删除站点及其全部版本",
                input_schema={
                    "type": "object",
                    "properties": {"slug": {"type": "string"}},
                    "required": ["slug"],
                },
                operation="delete",
            ),
            McpToolDef(
                name="site_access",
                description=(
                    "设置站点访问模式 public/token；token 模式下返回明文令牌和已拼好 ?token= 的可访问 url，"
                    "可直接发给用户。重置令牌时传 reset_token=true。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string"},
                        "mode": {"type": "string", "description": "public 或 token"},
                        "reset_token": {"type": "boolean", "description": "token 模式下是否强制重置令牌"},
                    },
                    "required": ["slug", "mode"],
                },
                operation="access",
            ),
            McpToolDef(
                name="site_access_info",
                description="查询站点当前访问模式、明文令牌（令牌模式）与可直接访问的 url，用于拿地址给用户。",
                input_schema={
                    "type": "object",
                    "properties": {"slug": {"type": "string"}},
                    "required": ["slug"],
                },
                operation="access_info",
            ),
            McpToolDef(
                name="site_update",
                description=(
                    "编辑站点元信息：显示名、描述、slug、入口文件、SPA 兜底、启用状态、访问模式。"
                    "只传需要改动的字段；未传的字段保持不变。slug 改动后预览地址随之变化。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string", "description": "要编辑的站点 slug"},
                        "name": {"type": "string", "description": "新的显示名"},
                        "description": {"type": "string", "description": "新的描述，空串清空"},
                        "new_slug": {"type": "string", "description": "新的 slug（改地址用）"},
                        "entry_file": {"type": "string", "description": "入口文件，空串重置为 index.html"},
                        "spa_fallback": {"type": "boolean", "description": "无扩展名路径是否回退到入口文件"},
                        "status": {"type": "string", "description": "active 或 disabled"},
                        "access_mode": {"type": "string", "description": "public 或 token"},
                    },
                    "required": ["slug"],
                },
                operation="update",
            ),
        ]

    async def dispatch(self, operation: str, payload: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        if ctx.mcp_key is None:
            raise SiteError("需要 MCP Key", status_code=401, error_type="authentication_error")
        key_id = ctx.mcp_key.id
        if operation in {"deploy", "site_deploy"}:
            return self._deploy(ctx, payload, key_id)
        if operation in {"list", "site_list"}:
            rows, _total = service.list_sites(ctx.db, mcp_key_id=key_id)
            items = [
                service.site_payload(site, current=service.current_version(ctx.db, site)) for site in rows
            ]
            return {"items": items}
        if operation in {"status", "site_status"}:
            site = service.get_site_by_slug(ctx.db, str(payload.get("slug") or ""), key_id)
            return service.site_detail_payload(ctx.db, site)
        if operation in {"rollback", "site_rollback"}:
            site = service.get_site_by_slug(ctx.db, str(payload.get("slug") or ""), key_id)
            version = service.rollback(ctx.db, site, int(payload.get("version_no") or 0))
            return {"site": service.site_payload(site, current=version), "version": service.version_payload(version, current_version_id=site.current_version_id)}
        if operation in {"delete", "site_delete"}:
            site = service.get_site_by_slug(ctx.db, str(payload.get("slug") or ""), key_id)
            slug = site.slug
            service.delete_site(ctx.db, site)
            return {"deleted": True, "slug": slug}
        if operation in {"access", "site_access"}:
            site = service.get_site_by_slug(ctx.db, str(payload.get("slug") or ""), key_id)
            site, token = service.set_access(
                ctx.db,
                site,
                mode=str(payload.get("mode") or ""),
                reset_token=bool(payload.get("reset_token")),
            )
            body = service.access_info(ctx.db, site)
            body["token"] = token
            body["url"] = service.access_url(site, token)
            return body
        if operation in {"access_info", "site_access_info"}:
            site = service.get_site_by_slug(ctx.db, str(payload.get("slug") or ""), key_id)
            return service.access_info(ctx.db, site)
        if operation in {"update", "site_update"}:
            site = service.get_site_by_slug(ctx.db, str(payload.get("slug") or ""), key_id)
            # new_slug 是工具入参名，服务层字段叫 slug（入参的 slug 已被用作定位站点）。
            fields: dict[str, Any] = {}
            if "new_slug" in payload:
                fields["slug"] = payload["new_slug"]
            for name in ("name", "description", "entry_file", "spa_fallback", "status", "access_mode"):
                if name in payload:
                    fields[name] = payload[name]
            service.update_site(ctx.db, site, **fields)
            return service.site_detail_payload(ctx.db, site)
        raise SiteError(f"未知操作: {operation}", status_code=404, error_type="not_found")

    def _deploy(self, ctx: CallContext, payload: dict[str, Any], key_id: int) -> dict[str, Any]:
        filename = str(payload.get("filename") or "")
        encoded = str(payload.get("archive_base64") or "")
        if not filename or not encoded:
            raise SiteError("filename 与 archive_base64 必填")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise SiteError("archive_base64 不是合法 base64") from error
        if len(raw) > get_settings().site_mcp_max_bytes:
            raise SiteError(
                f"MCP 入参超过 {get_settings().site_mcp_max_bytes} 字节，请改用 REST multipart"
            )
        site, version = service.create_deploy(
            ctx.db,
            archive_bytes=raw,
            filename=filename,
            created_by="key",
            mcp_key_id=key_id,
            slug=(str(payload.get("slug")).strip() if payload.get("slug") else None),
            name=(str(payload.get("name")).strip() if payload.get("name") else None),
            entry=(str(payload.get("entry")).strip() if payload.get("entry") else None),
            activate=bool(payload.get("activate", True)),
        )
        service.run_deploy(ctx.db, version, activate=bool(payload.get("activate", True)))
        return {
            "site": service.site_payload(site, current=service.current_version(ctx.db, site)),
            "version": service.version_payload(version, current_version_id=site.current_version_id),
            "preview_url": service.preview_url(site.slug),
        }

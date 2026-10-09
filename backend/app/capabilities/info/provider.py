from __future__ import annotations

from typing import Any

from app.capabilities.base import CallContext, CapabilitySpec, McpToolDef
from app.config import get_settings
from app.info import report
from app.info.errors import InfoError


class InfoReportProvider:
    spec = CapabilitySpec(
        capability_id="info",
        name="资讯上报",
        description="外部 Agent 上送资讯（文本 + 图片/视频字节），入库后由 AI 判定可见性",
        version="1.0.0",
        category="content",
        status="enabled",
        admin_path="/info",
        icon="newspaper",
        input_schema={
            "report": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "author": {"type": "string"},
                    "published_at": {"type": "string"},
                    "external_id": {"type": "string"},
                    "media": {"type": "array", "items": {"type": "object"}},
                },
            },
            "report_batch": {
                "type": "object",
                "properties": {"items": {"type": "array", "items": {"type": "object"}}},
                "required": ["items"],
            },
            "config": {"type": "object", "properties": {}},
        },
        integration={
            "rest_endpoints": [
                {
                    "method": "POST",
                    "path": "/v1/info/report",
                    "summary": "上报单条资讯（multipart 可带媒体）",
                    "content_type": "multipart/form-data",
                },
                {
                    "method": "POST",
                    "path": "/v1/info/report/batch",
                    "summary": "批量上报（JSON，媒体用 base64）",
                    "content_type": "application/json",
                },
                {
                    "method": "GET",
                    "path": "/v1/info/report/config",
                    "summary": "查看上报开关与各项上限",
                },
            ],
            "notes": [
                "text 与媒体至少其一；REST multipart 与 MCP base64 单文件各有上限",
                "媒体仅支持 jpg/png/webp/gif 图片与 mp4/webm/mov 视频（不接受 svg）",
                "external_id 缺省时按 url+text+媒体摘要去重，重复上报返回 duplicate=true",
                "入库后由 AI 判定精选/隐藏；上报内容归属内置「其他」渠道",
            ],
        },
    )

    def list_mcp_tools(self) -> list[McpToolDef]:
        limit = get_settings().info_report_max_mcp_file_bytes
        return [
            McpToolDef(
                name="info_report",
                description="上报一条资讯，可带图片/视频（base64）。入库后由 AI 判定精选。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "正文；与 media 至少其一"},
                        "title": {"type": "string", "description": "标题（可选）"},
                        "url": {"type": "string", "description": "原始链接（可选）"},
                        "author": {"type": "string", "description": "作者；缺省用 MCP Key 名"},
                        "published_at": {"type": "string", "description": "ISO8601 发布时间（可选）"},
                        "external_id": {
                            "type": "string",
                            "description": "去重键；缺省按 url+text+媒体摘要生成",
                        },
                        "media": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "filename": {"type": "string"},
                                    "content_base64": {"type": "string"},
                                    "kind": {"type": "string", "enum": ["image", "video"]},
                                    "duration_ms": {"type": "integer"},
                                },
                                "required": ["filename", "content_base64"],
                            },
                            "description": f"图片/视频，单文件解码后不超过 {limit} 字节",
                        },
                    },
                },
                operation="report",
            ),
            McpToolDef(
                name="info_report_batch",
                description="批量上报资讯（每条可选带 base64 媒体），逐条独立处理，单条失败不影响其余。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "info_report 入参组成的数组",
                        }
                    },
                    "required": ["items"],
                },
                operation="report_batch",
            ),
            McpToolDef(
                name="info_report_config",
                description="查看资讯上报的开关状态与各项上限。",
                input_schema={"type": "object", "properties": {}},
                operation="config",
            ),
        ]

    async def dispatch(
        self, operation: str, payload: dict[str, Any], ctx: CallContext
    ) -> dict[str, Any]:
        if ctx.mcp_key is None:
            raise InfoError("需要 MCP Key", status_code=401, error_type="authentication_error")
        if operation in {"report", "info_report"}:
            item = self._parse_item(payload)
            return report.save_report(ctx.db, mcp_key=ctx.mcp_key, item=item)
        if operation in {"report_batch", "info_report_batch"}:
            raw_items = payload.get("items")
            if not isinstance(raw_items, list):
                raise InfoError("items 必须为数组", status_code=400, error_type="invalid_request")
            items = [self._parse_item(entry) for entry in raw_items if isinstance(entry, dict)]
            return report.save_reports(ctx.db, mcp_key=ctx.mcp_key, items=items)
        if operation in {"config", "info_report_config"}:
            return report.limits()
        raise InfoError(f"未知操作: {operation}", status_code=404, error_type="not_found")

    def _parse_item(self, payload: dict[str, Any]) -> report.ReportInput:
        media = report.parse_json_media(
            payload.get("media"), max_bytes=get_settings().info_report_max_mcp_file_bytes
        )
        return report.build_input(payload, media)

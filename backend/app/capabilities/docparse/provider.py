from __future__ import annotations

import base64
import binascii
from typing import Any

from app.capabilities.base import CallContext, CapabilitySpec, McpToolDef
from app.config import get_settings

from .errors import DocParseError
from .jobs import create_job, get_owned_job, job_payload, run_job


class DocParseProvider:
    spec = CapabilitySpec(
        capability_id="docparse",
        name="文档转 Markdown",
        description="将 Word、PDF、Excel、PPT、HTML 转为 Markdown",
        version="1.0.0",
        category="document",
        status="enabled",
        admin_path="/mcp-plaza/docparse",
        icon="file-text",
        input_schema={
            "convert": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "content_base64": {"type": "string"},
                },
                "required": ["filename", "content_base64"],
            },
            "job": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
            "result": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
        },
    )

    def list_mcp_tools(self) -> list[McpToolDef]:
        limit = get_settings().doc_parse_mcp_max_bytes
        return [
            McpToolDef(
                name="docparse_convert",
                description=f"提交办公文档并转换为 Markdown。content_base64 解码后不超过 {limit} 字节，更大文件走 REST multipart。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "原始文件名，含扩展名"},
                        "content_base64": {"type": "string", "description": "文件内容的 base64"},
                    },
                    "required": ["filename", "content_base64"],
                },
                operation="convert",
            ),
            McpToolDef(
                name="docparse_job",
                description="查询当前 Key 的文档转换任务状态",
                input_schema={
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                },
                operation="job",
            ),
            McpToolDef(
                name="docparse_result",
                description="读取转换成功的 Markdown。超过 1MB 时截断并给出下载路径。",
                input_schema={
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                },
                operation="result",
            ),
        ]

    async def dispatch(self, operation: str, payload: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        if ctx.mcp_key is None:
            raise DocParseError("需要 MCP Key", status_code=401, error_type="authentication_error")
        key_id = ctx.mcp_key.id
        if operation in {"convert", "docparse_convert"}:
            return await self._convert(ctx, payload, key_id)
        if operation in {"job", "docparse_job"}:
            job = get_owned_job(ctx.db, str(payload.get("job_id") or ""), key_id)
            return job_payload(job)
        if operation in {"result", "docparse_result"}:
            job = get_owned_job(ctx.db, str(payload.get("job_id") or ""), key_id)
            if job.status != "succeeded":
                raise DocParseError("任务尚未成功，不能读取结果")
            body = job_payload(job, include_markdown=True)
            if body.get("truncated"):
                body["download_path"] = f"/v1/capabilities/docparse/jobs/{job.id}/download"
            return body
        raise DocParseError(f"未知操作: {operation}", status_code=404, error_type="not_found")

    async def _convert(self, ctx: CallContext, payload: dict[str, Any], key_id: int) -> dict[str, Any]:
        filename = str(payload.get("filename") or "")
        encoded = str(payload.get("content_base64") or "")
        if not filename or not encoded:
            raise DocParseError("filename 与 content_base64 必填")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise DocParseError("content_base64 不是合法 base64") from error
        if len(raw) > get_settings().doc_parse_mcp_max_bytes:
            raise DocParseError(
                f"MCP 入参超过 {get_settings().doc_parse_mcp_max_bytes} 字节，请改用 REST multipart"
            )
        job = create_job(ctx.db, filename=filename, raw=raw, created_by="key", mcp_key_id=key_id)
        try:
            run_job(ctx.db, job, raw)
        except DocParseError:
            ctx.db.flush()
        return job_payload(job)

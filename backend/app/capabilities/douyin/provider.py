from __future__ import annotations

import time
from typing import Any

from app.capabilities.base import CallContext, CapabilitySpec, McpToolDef
from app.config import get_settings

from . import jobs
from .errors import DouyinError


class DouyinProvider:
    spec = CapabilitySpec(
        capability_id="douyin",
        name="抖音视频下载",
        description="解析抖音分享链接或文案，转存视频/图集并提供稳定下载地址",
        version="1.0.0",
        category="media",
        status="enabled",
        admin_path="/mcp-plaza/douyin",
        icon="download",
        input_schema={
            "parse": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "share_text": {"type": "string"},
                    "rehost": {"type": "boolean"},
                    "wait_seconds": {"type": "integer"},
                },
            },
            "job": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
            "result": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
            "list": {"type": "object", "properties": {"limit": {"type": "integer"}}},
        },
        integration={
            "rest_endpoints": [
                {
                    "method": "POST",
                    "path": "/v1/douyin/parse",
                    "summary": "解析分享文案/直链并转存媒体",
                    "content_type": "application/json",
                },
                {"method": "GET", "path": "/v1/douyin/jobs", "summary": "列出当前 Key 的下载任务"},
                {"method": "GET", "path": "/v1/douyin/jobs/{job_id}", "summary": "查询任务状态与进度"},
                {"method": "GET", "path": "/v1/douyin/jobs/{job_id}/result", "summary": "读取任务结果与媒体下载地址"},
                {"method": "GET", "path": "/v1/douyin/media/{media_id}", "summary": "下载媒体文件（令牌或归属 Key）"},
            ],
            "notes": [
                "输入支持分享文案或直链，系统自动提取其中首个抖音链接",
                "rehost=true（默认）时平台转存并返回稳定下载地址；rehost=false 仅返回元数据与原始直链",
                "download_url 为相对路径 /v1/douyin/media/{id}?token=...，按部署地址拼接即可；absolute_download_url 为带网关根地址的绝对地址",
                "解析走 TikHub 托管 API：请在管理页「TikHub 解析 API」配置 Base URL 与 API Key（加密存储），也可用环境变量 DOUYIN_TIKHUB_BASE_URL / DOUYIN_TIKHUB_API_KEY",
                "提交即返回：解析（resolving）与下载（downloading，含 downloaded_bytes/expected_bytes/percent）在后台执行；MCP douyin_parse 在等待窗口内尽量完成，超时返回 job_id，可用 douyin_job 轮询推进",
                "图集作品的图片按 index_no 保序；单作品媒体项数量与大小受环境变量限制",
            ],
        },
    )

    def list_mcp_tools(self) -> list[McpToolDef]:
        return [
            McpToolDef(
                name="douyin_parse",
                description=(
                    "解析抖音分享文案或直链，转存视频/图集并返回稳定下载地址。"
                    "输入 url 或 share_text 二选一。rehost=false 时只解析不下载。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "抖音直链"},
                        "share_text": {"type": "string", "description": "抖音分享文案（含短链）"},
                        "rehost": {"type": "boolean", "description": "是否转存媒体，默认 true"},
                        "wait_seconds": {"type": "integer", "description": "内联等待转存的秒数上限"},
                    },
                },
                operation="parse",
            ),
            McpToolDef(
                name="douyin_job",
                description="查询或推进抖音下载任务；未完成时会在等待窗口内继续转存。",
                input_schema={
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                },
                operation="job",
            ),
            McpToolDef(
                name="douyin_result",
                description="读取任务完整结果，包含每个媒体项的原始直链与平台下载地址。",
                input_schema={
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                },
                operation="result",
            ),
            McpToolDef(
                name="douyin_list",
                description="列出当前 Key 创建的抖音下载任务。",
                input_schema={
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100}},
                },
                operation="list",
            ),
        ]

    async def dispatch(self, operation: str, payload: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        if ctx.mcp_key is None:
            raise DouyinError("需要 MCP Key", status_code=401, error_type="authentication_error")
        key_id = ctx.mcp_key.id
        if operation in {"parse", "douyin_parse"}:
            return self._parse(ctx, payload, key_id)
        if operation in {"job", "douyin_job"}:
            job = jobs.get_job(ctx.db, str(payload.get("job_id") or ""), key_id)
            if job.status not in jobs.TERMINAL_STATUSES:
                jobs.run_job(
                    ctx.db,
                    job,
                    deadline=time.monotonic() + get_settings().douyin_mcp_max_wait_seconds,
                    persist=True,
                )
            return jobs.job_result_payload(ctx.db, job)
        if operation in {"result", "douyin_result"}:
            job = jobs.get_job(ctx.db, str(payload.get("job_id") or ""), key_id)
            return jobs.job_result_payload(ctx.db, job)
        if operation in {"list", "douyin_list"}:
            limit = int(payload.get("limit") or 20)
            rows, _total = jobs.list_jobs(ctx.db, mcp_key_id=key_id, limit=max(1, min(limit, 100)))
            return {"items": [jobs.job_payload(job) for job in rows]}
        raise DouyinError(f"未知操作: {operation}", status_code=404, error_type="not_found")

    def _parse(self, ctx: CallContext, payload: dict[str, Any], key_id: int) -> dict[str, Any]:
        raw = self._raw_input(payload)
        rehost_value = payload.get("rehost", True)
        rehost = True if rehost_value is None else bool(rehost_value)
        job = jobs.create_job(
            ctx.db, raw_input=raw, rehost=rehost, created_by="key", mcp_key_id=key_id
        )
        wait = payload.get("wait_seconds")
        try:
            wait_seconds = max(1, int(wait)) if wait is not None else get_settings().douyin_mcp_max_wait_seconds
        except (TypeError, ValueError):
            wait_seconds = get_settings().douyin_mcp_max_wait_seconds
        jobs.run_job(ctx.db, job, deadline=time.monotonic() + wait_seconds, persist=True)
        return jobs.job_result_payload(ctx.db, job)

    @staticmethod
    def _raw_input(payload: dict[str, Any]) -> str:
        url = str(payload.get("url") or "").strip()
        if url:
            return url
        share_text = str(payload.get("share_text") or "").strip()
        if share_text:
            return share_text
        raise DouyinError("url 或 share_text 至少填写一个")

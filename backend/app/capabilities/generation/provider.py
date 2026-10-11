"""图片 / 视频生成的 MCP 能力：把已有生成能力包装成 MCP 工具与 REST 端点。

模型不通过对外 API 传入，而是在「MCP 接入 → 该 Key 的能力配置」里选定；调用方只能
配置与模型匹配的参数（比例 / 尺寸 / 时长等）与参考素材。作品按 Key 隔离。
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any
from urllib.parse import urljoin

import httpx

from app.capabilities.base import CallContext, CapabilityError, CapabilitySpec, McpToolDef
from app.config import get_settings
from app.db import get_session_factory
from app.generation import catalog, service, storage
from app.info.errors import InfoError
from app.info.urlguard import assert_safe_url
from app.services.mcp_key_config import get_capability_config

CAPABILITY_ID = "generation"

_PARAM_NOTE = "参数键随模型而定，可先调用后按错误提示调整；常用键：aspectRatio、imageSize、quality、resolution、duration、seed。"

_MEDIA_NOTE = (
    "images / audios 支持 http(s) 链接或 data URL（data:image/png;base64,...），"
    "平台会先转存到本地再随任务提交。"
)

_MEDIA_ITEM = {
    "type": "string",
    "description": "http(s) 链接或 data URL（data:image/png;base64,...）",
}

_TASK_RETURN = (
    '{job_id, kind: "image"|"video", model, status: "queued"|"running"|"succeeded"|"failed"|"violation", '
    "progress: 0-100, error, params, inputs: [{id,kind,content_type,width,height,status,url}], "
    "results: [{id,kind,content_type,width,height,status,error_message,remote_url,url,thumb_url,download_url}], "
    "created_at, finished_at}；status=succeeded 且结果 status=ready 时 download_url 可直接下载，未转存完时可用 remote_url。"
)

_MAX_REDIRECTS = 3


def _fetch_remote(url: str, max_bytes: int) -> tuple[bytes, str]:
    """下载远程参考素材：逐跳校验主机（拒绝内网/回环），限制重定向次数与体积，防 SSRF。"""
    settings = get_settings()
    timeout = httpx.Timeout(max(10, settings.generation_http_timeout_seconds), connect=10)
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        try:
            assert_safe_url(current)
        except InfoError as error:
            raise CapabilityError(error.message, status_code=400, error_type=error.error_type) from error
        try:
            with (
                httpx.Client(timeout=timeout, follow_redirects=False) as client,
                client.stream("GET", current) as response,
            ):
                if response.is_redirect and response.headers.get("location"):
                    current = urljoin(current, response.headers["location"])
                    continue
                if response.status_code >= 400:
                    raise CapabilityError(
                        f"下载参考素材失败：HTTP {response.status_code}",
                        status_code=400,
                        error_type="invalid_request",
                    )
                content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    # 边下边校验，避免恶意大响应在体积检查前就撑爆内存
                    if size > max_bytes:
                        raise CapabilityError("参考素材超过大小上限", status_code=400, error_type="invalid_request")
                    chunks.append(chunk)
                return b"".join(chunks), content_type
        except httpx.HTTPError as error:
            raise CapabilityError(f"下载参考素材失败：{error}", status_code=400, error_type="invalid_request") from error
    raise CapabilityError("参考素材重定向次数过多", status_code=400, error_type="invalid_request")


class GenerationProvider:
    spec = CapabilitySpec(
        capability_id=CAPABILITY_ID,
        name="图片 / 视频生成",
        description="用平台已接入的生成供应商出图 / 出视频；模型在 MCP Key 配置中选定，调用方只传提示词与参数",
        version="1.0.0",
        category="media",
        status="enabled",
        admin_path="/mcp-plaza/generation",
        icon="sparkles",
        input_schema={
            "image_generate": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "params": {"type": "object"},
                    "images": {"type": "array", "items": {"type": "string"}},
                    "wait_seconds": {"type": "integer"},
                },
                "required": ["prompt"],
            },
            "video_generate": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "params": {"type": "object"},
                    "images": {"type": "array", "items": {"type": "string"}},
                    "audios": {"type": "array", "items": {"type": "string"}},
                    "wait_seconds": {"type": "integer"},
                },
                "required": ["prompt"],
            },
            "generation_job": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}, "wait_seconds": {"type": "integer"}},
                "required": ["job_id"],
            },
            "generation_result": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
            "generation_list": {
                "type": "object",
                "properties": {"kind": {"type": "string"}, "limit": {"type": "integer"}},
            },
        },
        integration={
            "rest_endpoints": [
                {
                    "method": "POST",
                    "path": "/v1/capabilities/generation/image_generate",
                    "summary": "按 Key 配置的图片模型生成图片",
                    "content_type": "application/json",
                },
                {
                    "method": "POST",
                    "path": "/v1/capabilities/generation/video_generate",
                    "summary": "按 Key 配置的视频模型生成视频",
                    "content_type": "application/json",
                },
                {"method": "POST", "path": "/v1/capabilities/generation/generation_job", "summary": "查询/推进生成任务"},
                {"method": "POST", "path": "/v1/capabilities/generation/generation_result", "summary": "读取任务结果与下载地址"},
                {"method": "POST", "path": "/v1/capabilities/generation/generation_list", "summary": "列出当前 Key 的生成任务"},
            ],
            "notes": [
                "模型不通过接口传入：请在管理端「MCP 广场 → 服务目录 → 图片 / 视频生成」中为该能力选定图片 / 视频模型。",
                f"{_PARAM_NOTE}",
                f"{_MEDIA_NOTE}",
                "提交后返回 job_id 与状态；可在等待窗口内直接返回结果，未完成时用 generation_job 轮询。",
                "download_url 为带网关根地址的绝对地址，按 Key 令牌可匿名下载；结果也会转存到平台本地。",
            ],
        },
    )

    def list_mcp_tools(self) -> list[McpToolDef]:
        return [
            McpToolDef(
                name="image_generate",
                description="按当前 Key 配置的图片模型生成图片。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "图片提示词（必填）"},
                        "params": {
                            "type": "object",
                            "description": "生成参数对象，键随模型而定（如 aspectRatio / imageSize / quality / background）",
                        },
                        "images": {
                            "type": "array",
                            "items": _MEDIA_ITEM,
                            "description": "参考图列表（图生图 / 多图参考），最多 9 张",
                        },
                        "wait_seconds": {
                            "type": "integer",
                            "description": "内联等待完成的秒数上限；超时返回进行中的 job，可用 generation_job 继续轮询",
                        },
                    },
                    "required": ["prompt"],
                },
                operation="image_generate",
                returns=_TASK_RETURN,
            ),
            McpToolDef(
                name="video_generate",
                description="按当前 Key 配置的视频模型生成视频。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "视频提示词（主体、运镜、时长、声音等）（必填）",
                        },
                        "params": {
                            "type": "object",
                            "description": "生成参数对象（如 aspectRatio: portrait|landscape、resolution: 480p|768p|1080p、duration: 1-15、seed）",
                        },
                        "images": {
                            "type": "array",
                            "items": _MEDIA_ITEM,
                            "description": "参考图列表，最多 9 张",
                        },
                        "audios": {
                            "type": "array",
                            "items": _MEDIA_ITEM,
                            "description": "参考音频列表，最多 3 个",
                        },
                        "wait_seconds": {"type": "integer", "description": "内联等待完成的秒数上限"},
                    },
                    "required": ["prompt"],
                },
                operation="video_generate",
                returns=_TASK_RETURN,
            ),
            McpToolDef(
                name="generation_job",
                description="查询或推进生成任务；未完成时会在等待窗口内继续轮询。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "image_generate / video_generate 返回的 job_id（必填）"},
                        "wait_seconds": {"type": "integer", "description": "本次最多等待推进的秒数"},
                    },
                    "required": ["job_id"],
                },
                operation="generation_job",
                returns=_TASK_RETURN,
            ),
            McpToolDef(
                name="generation_result",
                description="读取生成任务结果，包含每个结果文件的下载地址。",
                input_schema={
                    "type": "object",
                    "properties": {"job_id": {"type": "string", "description": "任务 id（必填）"}},
                    "required": ["job_id"],
                },
                operation="generation_result",
                returns=_TASK_RETURN,
            ),
            McpToolDef(
                name="generation_list",
                description="列出当前 Key 创建的生成任务。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "description": "可选，按类型过滤：image 或 video"},
                        "limit": {"type": "integer", "description": "返回条数，默认 20，最大 100"},
                    },
                },
                operation="generation_list",
                returns='{items: [同上任务结构], total: int}',
            ),
        ]

    async def dispatch(self, operation: str, payload: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        if ctx.mcp_key is None:
            raise CapabilityError("需要 MCP Key", status_code=401, error_type="authentication_error")
        key_id = ctx.mcp_key.id
        try:
            if operation in {"image_generate", "image"}:
                return await self._generate(ctx, payload, "image")
            if operation in {"video_generate", "video"}:
                return await self._generate(ctx, payload, "video")
            if operation in {"generation_job", "job"}:
                return await self._job(ctx, payload)
            if operation in {"generation_result", "result"}:
                return self._result(ctx, payload)
            if operation in {"generation_list", "list"}:
                return self._list(ctx, payload, key_id)
        except CapabilityError:
            raise
        except service.GenerationError as error:
            raise CapabilityError(error.message, status_code=error.status_code, error_type="invalid_request") from error
        except Exception as error:  # noqa: BLE001
            raise CapabilityError(str(error), status_code=500, error_type="internal_error") from error
        raise CapabilityError(f"未知操作: {operation}", status_code=404, error_type="not_found")

    # ------------------------------------------------------------------ #

    async def _generate(self, ctx: CallContext, payload: dict[str, Any], kind: str) -> dict[str, Any]:
        assert ctx.mcp_key is not None
        model = self._resolve_model(ctx, kind)
        if not model:
            raise CapabilityError(
                f"尚未配置{'图片' if kind == 'image' else '视频'}模型，请在「MCP 广场 → 服务目录 → 图片 / 视频生成」中选择",
                status_code=400,
                error_type="invalid_request",
            )
        spec = catalog.get_model(model)
        if spec is None or spec.kind != kind:
            raise CapabilityError("配置的模型不可用", status_code=400, error_type="invalid_request")
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise CapabilityError("缺少 prompt", status_code=400, error_type="invalid_request")
        params = self._collect_params(spec, payload)
        task = service.create_task(
            ctx.db,
            kind=kind,
            model=model,
            prompt=prompt,
            params=params,
            mcp_key_id=ctx.mcp_key.id,
        )
        max_bytes = get_settings().generation_max_input_bytes
        try:
            for item in payload.get("images") or []:
                raw, ctype = await asyncio.to_thread(self._load_source, item, max_bytes)
                service.add_task_input(ctx.db, task, data=raw, content_type=ctype)
            if spec.supports_audios:
                for item in payload.get("audios") or []:
                    raw, ctype = await asyncio.to_thread(self._load_source, item, max_bytes)
                    service.add_task_input(ctx.db, task, data=raw, content_type=ctype)
            ctx.db.commit()
        except Exception:
            # 任务尚未落库，清掉已写入的参考文件，避免残留
            ctx.db.rollback()
            storage.purge_task(task.id)
            raise
        wait = self._wait_seconds(payload.get("wait_seconds"))
        await asyncio.to_thread(service.run_task_inline, task.id, wait)
        return await asyncio.to_thread(self._payload, ctx, task.id)

    def _resolve_model(self, ctx: CallContext, kind: str) -> str:
        """模型来源：Key 级覆盖（如有）→ 服务目录中配置的全局默认模型。"""
        config = get_capability_config(ctx.mcp_key, CAPABILITY_ID)
        override = str(config.get(f"{kind}_model") or "").strip()
        if override:
            return override
        payload = service.settings_payload(ctx.db)
        return str(payload.get(f"default_{kind}_model") or "").strip()

    def _collect_params(self, spec: catalog.ModelSpec, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {param.key for param in spec.params}
        merged: dict[str, Any] = {}
        nested = payload.get("params")
        if isinstance(nested, dict):
            merged.update(nested)
        for key in allowed:
            if key in payload:
                merged.setdefault(key, payload[key])
        return {key: value for key, value in merged.items() if key in allowed}

    @staticmethod
    def _wait_seconds(value: Any) -> int:
        if value is None:
            return get_settings().generation_mcp_max_wait_seconds
        try:
            return max(0, min(int(value), 600))
        except (TypeError, ValueError):
            return get_settings().generation_mcp_max_wait_seconds

    @staticmethod
    def _load_source(item: Any, max_bytes: int) -> tuple[bytes, str]:
        if isinstance(item, dict):
            item = item.get("url") or item.get("data") or item.get("base64") or ""
        text = str(item or "").strip()
        if not text:
            raise CapabilityError("参考素材为空", status_code=400, error_type="invalid_request")
        if text.startswith("data:"):
            header, _, encoded = text.partition(",")
            content_type = header[5:].split(";")[0].strip()
            try:
                raw = base64.b64decode(encoded)
            except (ValueError, TypeError) as error:
                raise CapabilityError("data URL 解析失败", status_code=400, error_type="invalid_request") from error
        elif text.startswith(("http://", "https://")):
            raw, content_type = _fetch_remote(text, max_bytes)
        else:
            raise CapabilityError("参考素材需为 http(s) 链接或 data URL", status_code=400, error_type="invalid_request")
        if len(raw) > max_bytes:
            raise CapabilityError("参考素材超过大小上限", status_code=400, error_type="invalid_request")
        return raw, content_type

    async def _job(self, ctx: CallContext, payload: dict[str, Any]) -> dict[str, Any]:
        assert ctx.mcp_key is not None
        task_id = str(payload.get("job_id") or "").strip()
        task = service.get_task_for_key(ctx.db, task_id, ctx.mcp_key.id)
        if task is None:
            raise CapabilityError("任务不存在", status_code=404, error_type="not_found")
        if task.status not in {"succeeded", "failed", "violation"}:
            wait = self._wait_seconds(payload.get("wait_seconds"))
            ctx.db.commit()
            await asyncio.to_thread(service.run_task_inline, task_id, wait)
        return await asyncio.to_thread(self._payload, ctx, task_id)

    def _result(self, ctx: CallContext, payload: dict[str, Any]) -> dict[str, Any]:
        assert ctx.mcp_key is not None
        task_id = str(payload.get("job_id") or "").strip()
        task = service.get_task_for_key(ctx.db, task_id, ctx.mcp_key.id)
        if task is None:
            raise CapabilityError("任务不存在", status_code=404, error_type="not_found")
        return self._payload(ctx, task_id)

    def _list(self, ctx: CallContext, payload: dict[str, Any], key_id: int) -> dict[str, Any]:
        kind = str(payload.get("kind") or "").strip()
        try:
            limit = int(payload.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        data = service.list_tasks(ctx.db, kind=kind, page=1, page_size=max(1, min(limit, 100)), mcp_key_id=key_id)
        return {"items": data["items"], "total": data["total"]}

    def _payload(self, ctx: CallContext, task_id: str) -> dict[str, Any]:
        # 用独立会话读取，避免拿到调用会话里被内联推进前的旧对象
        factory = get_session_factory()
        session = factory()
        try:
            task = service.get_task(session, task_id)
            if task is None:
                raise CapabilityError("任务不存在", status_code=404, error_type="not_found")
            data = service.task_to_dict(task, service.task_assets(session, task.id))
        finally:
            session.close()
        origin = get_settings().app_base_url.rstrip("/")
        for asset in data["results"]:
            if asset.get("url"):
                asset["download_url"] = f"{origin}{asset['url']}"
        return {
            "job_id": data["id"],
            "kind": data["kind"],
            "model": data["model"],
            "status": data["status"],
            "progress": data["progress"],
            "error": data["error"],
            "params": data["params"],
            "inputs": data["inputs"],
            "results": data["results"],
            "created_at": data["created_at"],
            "finished_at": data["finished_at"],
        }

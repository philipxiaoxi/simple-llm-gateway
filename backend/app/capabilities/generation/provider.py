"""图片 / 视频生成的 MCP 能力：拆成 image_gen 与 video_gen 两个独立能力。

调用方在 `model` 参数里选模型（`provider:model` 或裸模型名）；不传时依次回退到
MCP Key 配置、服务端默认模型、第一个可用模型。每个模型的参数与支持的素材类型不同，
所以额外提供 `<kind>_models` 工具 / `models` 端点，先把参数与返回值讲清楚。
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
from app.generation import catalog, registry, service, storage
from app.info.errors import InfoError
from app.info.urlguard import assert_safe_url
from app.services.mcp_key_config import get_capability_config

_PARAM_NOTE = (
    "params 只能使用该模型声明过的参数键；先调用 {prefix}_models 获取每个模型的可选值、"
    "默认范围与素材限制。传了模型不认识的键会直接报错并返回可用参数，避免静默丢弃。"
)

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

_MODELS_RETURN = (
    '{kind, default_model, total, models: [{model: "provider:model", provider, provider_label, label, '
    "available, unavailable_reason, params: [{key,label,type,options,default,min,max,required,help}], "
    "supports_images, max_images, supports_audios, max_audios, returns}], unavailable_providers, notes}；"
    "调用生成接口时把 models[].model 原样传给 model，params 只使用该条目的参数键。"
)

_MAX_REDIRECTS = 3

_KINDS: dict[str, dict[str, str]] = {
    "image": {
        "capability_id": "image_gen",
        "name": "图片生成",
        "noun": "图片",
        "prefix": "image",
        "icon": "image",
    },
    "video": {
        "capability_id": "video_gen",
        "name": "视频生成",
        "noun": "视频",
        "prefix": "video",
        "icon": "video",
    },
}


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
    """按 kind 实例化：image_gen 只管出图，video_gen 只管出视频。"""

    def __init__(self, kind: str = "image") -> None:
        meta = _KINDS.get(kind)
        if meta is None:
            raise ValueError(f"不支持的生成类型：{kind}")
        self.kind = kind
        self.prefix = meta["prefix"]
        self.noun = meta["noun"]
        self.capability_id = meta["capability_id"]
        self.spec = CapabilitySpec(
            capability_id=self.capability_id,
            name=meta["name"],
            description=(
                f"用平台已接入的生成供应商出{self.noun}；调用时用 model 选择模型，"
                f"先调用 {self.prefix}_models 了解参数与返回值"
            ),
            version="2.0.0",
            category="media",
            status="enabled",
            admin_path="/mcp-plaza/generation",
            icon=meta["icon"],
            input_schema=self._input_schema(),
            integration=self._integration(),
        )

    # ------------------------------------------------------------------ #
    # 声明：schema / tools / 接入说明
    # ------------------------------------------------------------------ #

    def _generate_properties(self) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "prompt": {
                "type": "string",
                "description": f"{self.noun}提示词（必填）",
            },
            "model": {
                "type": "string",
                "description": (
                    f"可选。先用 {self.prefix}_models 查可用模型；传 provider:model 或模型名。"
                    "不传时依次使用 MCP Key 配置、服务端默认模型、第一个可用模型。"
                ),
            },
            "params": {
                "type": "object",
                "description": (
                    f"生成参数对象，键随模型而定；先调用 {self.prefix}_models 获取该模型的参数、"
                    "可选值、默认范围与素材限制"
                ),
            },
            "images": {
                "type": "array",
                "items": _MEDIA_ITEM,
                "description": "参考图列表（图生图 / 多图参考），上限随模型而定",
            },
            "wait_seconds": {
                "type": "integer",
                "description": "可选，服务端内联等待完成的秒数上限；默认 0，立即返回 job_id",
            },
        }
        if self.kind == "video":
            properties["audios"] = {
                "type": "array",
                "items": _MEDIA_ITEM,
                "description": "参考音频列表，上限与是否支持随模型而定",
            }
        return properties

    def _input_schema(self) -> dict[str, Any]:
        prefix = self.prefix
        return {
            f"{prefix}_generate": {
                "type": "object",
                "properties": self._generate_properties(),
                "required": ["prompt"],
            },
            f"{prefix}_models": {
                "type": "object",
                "properties": {
                    "available_only": {
                        "type": "boolean",
                        "description": "默认 true：只返回已启用且配置了 Key 的供应商模型；传 false 返回全部并标注可用性",
                    }
                },
            },
            f"{prefix}_job": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "wait_seconds": {"type": "integer"},
                },
                "required": ["job_id"],
            },
            f"{prefix}_result": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
            f"{prefix}_list": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
            },
        }

    def _integration(self) -> dict[str, Any]:
        prefix = self.prefix
        cid = self.capability_id
        return {
            "rest_endpoints": [
                {
                    "method": "POST",
                    "path": f"/v1/capabilities/{cid}/{prefix}_generate",
                    "summary": f"生成{self.noun}；传 model 可切换模型",
                    "content_type": "application/json",
                },
                {
                    "method": "GET",
                    "path": f"/v1/capabilities/{cid}/models",
                    "summary": f"列出可用的{self.noun}模型、参数与返回值",
                },
                {
                    "method": "POST",
                    "path": f"/v1/capabilities/{cid}/{prefix}_job",
                    "summary": "查询生成任务（默认不等待）",
                },
                {
                    "method": "POST",
                    "path": f"/v1/capabilities/{cid}/{prefix}_result",
                    "summary": "读取任务结果与下载地址",
                },
                {
                    "method": "POST",
                    "path": f"/v1/capabilities/{cid}/{prefix}_list",
                    "summary": "列出当前 Key 的生成任务",
                },
            ],
            "notes": [
                f"先调用 {prefix}_models（或 GET /v1/capabilities/{cid}/models）拿到模型清单与参数说明，再一次调用成功。",
                "model 传 provider:model 或裸模型名；不传时回退到 Key 配置、服务端默认模型、第一个可用模型。",
                _PARAM_NOTE.format(prefix=prefix),
                _MEDIA_NOTE,
                "提交后立即返回 job_id 与初始状态，后台负责提交、轮询和结果转存。",
                "传 wait_seconds 可让本次调用在服务端等待推进（默认 0，立即返回），未完成时继续用 "
                f"{prefix}_job 轮询。",
                "结果转存完成前结果带 remote_url；转存完成后提供 download_url（带网关根地址的绝对地址，按 Key 令牌可匿名下载）。",
            ],
        }

    def list_mcp_tools(self) -> list[McpToolDef]:
        prefix = self.prefix
        return [
            McpToolDef(
                name=f"{prefix}_generate",
                description=(
                    f"生成{self.noun}：用 model 选择模型（先调用 {prefix}_models 了解参数），"
                    f"立即返回 job_id，用 {prefix}_job 查询进度与结果。"
                ),
                input_schema={
                    "type": "object",
                    "properties": self._generate_properties(),
                    "required": ["prompt"],
                },
                operation=f"{prefix}_generate",
                returns=_TASK_RETURN,
            ),
            McpToolDef(
                name=f"{prefix}_models",
                description=(
                    f"列出可用的{self.noun}模型及其参数、可选值、默认范围、素材限制与返回结构；"
                    f"调用 {prefix}_generate 前先用它确认 model 与 params。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "available_only": {
                            "type": "boolean",
                            "description": "默认 true；传 false 返回全部模型并标注可用性",
                        }
                    },
                },
                operation=f"{prefix}_models",
                returns=_MODELS_RETURN,
            ),
            McpToolDef(
                name=f"{prefix}_job",
                description="查询生成任务；默认立即返回，传 wait_seconds 可在服务端等待推进。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": f"{prefix}_generate 返回的 job_id（必填）",
                        },
                        "wait_seconds": {"type": "integer", "description": "可选，本次最多等待推进的秒数；默认 0"},
                    },
                    "required": ["job_id"],
                },
                operation=f"{prefix}_job",
                returns=_TASK_RETURN,
            ),
            McpToolDef(
                name=f"{prefix}_result",
                description="读取生成任务结果，包含每个结果文件的下载地址。",
                input_schema={
                    "type": "object",
                    "properties": {"job_id": {"type": "string", "description": "任务 id（必填）"}},
                    "required": ["job_id"],
                },
                operation=f"{prefix}_result",
                returns=_TASK_RETURN,
            ),
            McpToolDef(
                name=f"{prefix}_list",
                description=f"列出当前 Key 创建的{self.noun}生成任务。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "返回条数，默认 20，最大 100"},
                    },
                },
                operation=f"{prefix}_list",
                returns='{items: [同上任务结构], total: int}',
            ),
        ]

    # ------------------------------------------------------------------ #
    # 分发
    # ------------------------------------------------------------------ #

    async def dispatch(self, operation: str, payload: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        if ctx.mcp_key is None and not ctx.system:
            raise CapabilityError("需要 MCP Key", status_code=401, error_type="authentication_error")
        other = "video" if self.kind == "image" else "image"
        if operation == other or operation.startswith(f"{other}_"):
            raise CapabilityError(
                f"{operation} 属于 {_KINDS[other]['name']} 能力，请改用 {_KINDS[other]['capability_id']} 的端点和工具",
                status_code=400,
                error_type="invalid_request",
            )
        prefix = self.prefix
        handlers = {
            f"{prefix}_generate": self._generate,
            "generate": self._generate,
            f"{prefix}_models": self._models,
            "models": self._models,
            f"{prefix}_job": self._job,
            "job": self._job,
            "generation_job": self._job,
            f"{prefix}_result": self._result,
            "result": self._result,
            "generation_result": self._result,
            f"{prefix}_list": self._list,
            "list": self._list,
            "generation_list": self._list,
        }
        handler = handlers.get(operation)
        if handler is None:
            raise CapabilityError(f"未知操作: {operation}", status_code=404, error_type="not_found")
        try:
            return await handler(ctx, payload)
        except CapabilityError:
            raise
        except service.GenerationError as error:
            raise CapabilityError(error.message, status_code=error.status_code, error_type="invalid_request") from error
        except Exception as error:  # noqa: BLE001
            raise CapabilityError(str(error), status_code=500, error_type="internal_error") from error

    # ------------------------------------------------------------------ #
    # 模型解析
    # ------------------------------------------------------------------ #

    def _target_from_raw(self, ctx: CallContext, raw: str) -> tuple[str, catalog.ModelSpec]:
        provider, model = service.parse_model_ref(raw)
        if not provider:
            provider = service.find_provider_in(ctx.db, model, self.kind)
        return service.resolve_target(ctx.db, kind=self.kind, model=model, provider=provider)

    def _configured_ref(self, ctx: CallContext) -> str:
        if ctx.mcp_key is None:
            return ""
        config = get_capability_config(ctx.mcp_key, self.capability_id)
        return str(config.get("model") or config.get(f"{self.kind}_model") or "").strip()

    def _default_ref(self, ctx: CallContext) -> str:
        """当前默认模型：Key 配置 → 服务端默认 → 第一个可用模型；都不可用时返回空串。"""

        candidates = [self._configured_ref(ctx), service.default_model_ref(ctx.db, self.kind)]
        candidates += [f"{code}:{spec.id}" for code, spec in service.usable_models(ctx.db, self.kind)[:1]]
        seen: set[str] = set()
        for raw in candidates:
            raw = raw.strip()
            if not raw or raw in seen:
                continue
            seen.add(raw)
            try:
                code, spec = self._target_from_raw(ctx, raw)
            except service.GenerationError:
                continue
            if service.provider_unavailable_reason(ctx.db, code):
                continue
            return f"{code}:{spec.id}"
        return ""

    def _resolve_target(self, ctx: CallContext, raw_model: Any) -> tuple[str, str, catalog.ModelSpec]:
        explicit = str(raw_model or "").strip()
        if explicit:
            try:
                provider, spec = self._target_from_raw(ctx, explicit)
            except service.GenerationError as error:
                raise CapabilityError(
                    f"模型 {explicit} 不可用：{error.message}。先调用 {self.prefix}_models 查看可用模型",
                    status_code=400,
                    error_type="invalid_request",
                ) from error
            self._assert_usable(ctx, provider)
            return provider, spec.id, spec

        candidates = [self._configured_ref(ctx), service.default_model_ref(ctx.db, self.kind)]
        candidates += [f"{code}:{spec.id}" for code, spec in service.usable_models(ctx.db, self.kind)[:1]]
        reasons: list[str] = []
        tried: list[str] = []
        for raw in candidates:
            raw = str(raw or "").strip()
            if not raw or raw in tried:
                continue
            tried.append(raw)
            try:
                provider, spec = self._target_from_raw(ctx, raw)
            except service.GenerationError:
                continue
            reason = service.provider_unavailable_reason(ctx.db, provider)
            if reason:
                reasons.append(f"{provider} {reason}")
                continue
            return provider, spec.id, spec
        detail = f"（{'；'.join(dict.fromkeys(reasons))}）" if reasons else ""
        raise CapabilityError(
            f"没有可用的{self.noun}模型{detail}：请先在管理端配置供应商，或调用 {self.prefix}_models 查看可用模型",
            status_code=400,
            error_type="invalid_request",
        )

    def _assert_usable(self, ctx: CallContext, provider: str) -> None:
        reason = service.provider_unavailable_reason(ctx.db, provider)
        if reason:
            raise CapabilityError(
                f"供应商 {provider} 当前不可用：{reason}。先调用 {self.prefix}_models 查看可用模型",
                status_code=400,
                error_type="invalid_request",
            )

    # ------------------------------------------------------------------ #
    # 参数与素材校验
    # ------------------------------------------------------------------ #

    def _collect_params(self, spec: catalog.ModelSpec, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {param.key: param for param in spec.params}
        nested = payload.get("params")
        if nested is not None and not isinstance(nested, dict):
            raise CapabilityError("params 必须是对象", status_code=400, error_type="invalid_request")
        merged: dict[str, Any] = dict(nested) if isinstance(nested, dict) else {}
        for key in allowed:
            if key in payload:
                merged.setdefault(key, payload[key])

        unknown = sorted(str(key) for key in merged if key not in allowed)
        if unknown:
            names = "、".join(allowed) or "（无）"
            raise CapabilityError(
                f"模型 {spec.id} 不支持参数：{'、'.join(unknown)}。可用参数：{names}",
                status_code=400,
                error_type="invalid_request",
                diagnostics=[
                    {
                        "model": spec.id,
                        "unsupported_params": unknown,
                        "allowed_params": list(allowed),
                        "params": [param.to_dict() for param in spec.params],
                    }
                ],
            )

        cleaned: dict[str, Any] = {}
        for key, value in merged.items():
            if value is None or value == "":
                continue
            param = allowed[key]
            if param.type == "select" and param.options:
                values = [option.value for option in param.options]
                if str(value) not in values:
                    raise CapabilityError(
                        f"参数 {key}={value!r} 不在模型 {spec.id} 的可选范围内：{'、'.join(values)}",
                        status_code=400,
                        error_type="invalid_request",
                        diagnostics=[{"model": spec.id, "param": key, "allowed_values": values}],
                    )
            elif param.type == "number":
                try:
                    number = float(value)
                except (TypeError, ValueError) as error:
                    raise CapabilityError(
                        f"参数 {key} 必须是数字", status_code=400, error_type="invalid_request"
                    ) from error
                if param.minimum is not None and number < param.minimum:
                    raise CapabilityError(
                        f"参数 {key} 不能小于 {param.minimum}", status_code=400, error_type="invalid_request"
                    )
                if param.maximum is not None and number > param.maximum:
                    raise CapabilityError(
                        f"参数 {key} 不能大于 {param.maximum}", status_code=400, error_type="invalid_request"
                    )
            cleaned[key] = value
        return cleaned

    def _collect_media(
        self, spec: catalog.ModelSpec, payload: dict[str, Any]
    ) -> tuple[list[Any], list[Any]]:
        images = payload.get("images") or []
        if not isinstance(images, list):
            raise CapabilityError("images 必须是数组", status_code=400, error_type="invalid_request")
        if images and not spec.supports_images:
            raise CapabilityError(
                f"模型 {spec.id} 不支持参考图片", status_code=400, error_type="invalid_request"
            )
        if spec.max_images and len(images) > spec.max_images:
            raise CapabilityError(
                f"模型 {spec.id} 最多支持 {spec.max_images} 张参考图",
                status_code=400,
                error_type="invalid_request",
            )

        audios = payload.get("audios") or []
        if audios:
            if self.kind != "video":
                raise CapabilityError("图片生成不支持 audios", status_code=400, error_type="invalid_request")
            if not isinstance(audios, list):
                raise CapabilityError("audios 必须是数组", status_code=400, error_type="invalid_request")
            if not spec.supports_audios:
                raise CapabilityError(
                    f"模型 {spec.id} 不支持参考音频", status_code=400, error_type="invalid_request"
                )
            if spec.max_audios and len(audios) > spec.max_audios:
                raise CapabilityError(
                    f"模型 {spec.id} 最多支持 {spec.max_audios} 个参考音频",
                    status_code=400,
                    error_type="invalid_request",
                )
        return images, audios

    # ------------------------------------------------------------------ #
    # 操作
    # ------------------------------------------------------------------ #

    async def _generate(self, ctx: CallContext, payload: dict[str, Any]) -> dict[str, Any]:
        if ctx.mcp_key is None and not ctx.system:
            raise CapabilityError("需要 MCP Key", status_code=401, error_type="authentication_error")
        provider, model, spec = self._resolve_target(ctx, payload.get("model"))
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise CapabilityError("缺少 prompt", status_code=400, error_type="invalid_request")
        params = self._collect_params(spec, payload)
        images, audios = self._collect_media(spec, payload)
        task = service.create_task(
            ctx.db,
            kind=self.kind,
            model=model,
            prompt=prompt,
            params=params,
            provider=provider,
            mcp_key_id=None if ctx.system else ctx.mcp_key.id,
        )
        max_bytes = get_settings().generation_max_input_bytes
        try:
            for item in images:
                raw, ctype = await asyncio.to_thread(self._load_source, item, max_bytes)
                service.add_task_input(ctx.db, task, data=raw, content_type=ctype)
            for item in audios:
                raw, ctype = await asyncio.to_thread(self._load_source, item, max_bytes)
                service.add_task_input(ctx.db, task, data=raw, content_type=ctype)
            ctx.db.commit()
        except Exception:
            # 任务尚未落库，清掉已写入的参考文件，避免残留
            ctx.db.rollback()
            storage.purge_task(task.id)
            raise
        wait = self._wait_seconds(payload.get("wait_seconds"))
        if wait > 0:
            await asyncio.to_thread(service.run_task_inline, task.id, wait)
        return await asyncio.to_thread(self._payload, ctx, task.id)

    async def _models(self, ctx: CallContext, payload: dict[str, Any]) -> dict[str, Any]:
        raw_flag = payload.get("available_only")
        available_only = True if raw_flag is None else bool(raw_flag)
        entries: list[dict[str, Any]] = []
        unavailable: list[dict[str, str]] = []
        for code, info in registry.PROVIDERS.items():
            if not info.supports(self.kind):
                continue
            reason = service.provider_unavailable_reason(ctx.db, code)
            if reason and available_only:
                unavailable.append({"provider": code, "provider_label": info.label, "reason": reason})
                continue
            for spec in service.models_for_provider(ctx.db, code, self.kind):
                entries.append(self._model_entry(code, info.label, spec, reason))
        return {
            "kind": self.kind,
            "default_model": self._default_ref(ctx),
            "total": len(entries),
            "models": entries,
            "unavailable_providers": unavailable,
            "returns": _TASK_RETURN,
            "notes": [
                f"调用 {self.prefix}_generate 时把 model 原样传入，params 只使用该模型 params 里的键。",
                "available=false 的模型当前不可用（供应商停用或缺少 Key），不要选择。",
                "不确定用哪个模型时可不传 model，服务端会使用 default_model；仍建议先确认参数。",
            ],
        }

    @staticmethod
    def _model_entry(
        provider: str, provider_label: str, spec: catalog.ModelSpec, unavailable_reason: str
    ) -> dict[str, Any]:
        return {
            "model": f"{provider}:{spec.id}",
            "provider": provider,
            "provider_label": provider_label,
            "label": spec.label,
            "available": not unavailable_reason,
            "unavailable_reason": unavailable_reason,
            "params": [param.to_dict() for param in spec.params],
            "supports_images": spec.supports_images,
            "max_images": spec.max_images,
            "supports_audios": spec.supports_audios,
            "max_audios": spec.max_audios,
            "returns": _TASK_RETURN,
        }

    @staticmethod
    def _wait_seconds(value: Any) -> int:
        """默认 0：提交后立即返回，任务交给后台循环推进；显式传值才内联等待。"""
        try:
            return max(0, min(int(value), 600))
        except (TypeError, ValueError):
            return 0

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
        task_id = str(payload.get("job_id") or "").strip()
        task = (
            service.get_task(ctx.db, task_id)
            if ctx.system
            else service.get_task_for_key(ctx.db, task_id, ctx.mcp_key.id)  # type: ignore[union-attr]
        )
        if task is None:
            raise CapabilityError("任务不存在", status_code=404, error_type="not_found")
        if task.status not in {"succeeded", "failed", "violation"}:
            wait = self._wait_seconds(payload.get("wait_seconds"))
            if wait > 0:
                ctx.db.commit()
                await asyncio.to_thread(service.run_task_inline, task_id, wait)
        return await asyncio.to_thread(self._payload, ctx, task_id)

    async def _result(self, ctx: CallContext, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = str(payload.get("job_id") or "").strip()
        task = (
            service.get_task(ctx.db, task_id)
            if ctx.system
            else service.get_task_for_key(ctx.db, task_id, ctx.mcp_key.id)  # type: ignore[union-attr]
        )
        if task is None:
            raise CapabilityError("任务不存在", status_code=404, error_type="not_found")
        return await asyncio.to_thread(self._payload, ctx, task_id)

    async def _list(self, ctx: CallContext, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            limit = int(payload.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        data = service.list_tasks(
            ctx.db,
            kind=self.kind,
            page=1,
            page_size=max(1, min(limit, 100)),
            mcp_key_id=None if ctx.system else ctx.mcp_key.id,  # type: ignore[union-attr]
        )
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

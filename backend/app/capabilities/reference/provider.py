"""参考图库 MCP 能力：人像 / 姿势 / 服装各注册一个 provider。

调用方拿到 `image_url` 后，直接作为 `image_generate.images` 的参考图传入即可。
姿势与服装库含 18+ 分类，默认过滤；显式传 `include_adult=true` 才会返回。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.capabilities.base import CallContext, CapabilitySpec, McpToolDef
from app.capabilities.reference.errors import ReferenceError
from app.capabilities.reference.source import ATTRIBUTION, get_source
from app.capabilities.reference.specs import ReferenceSpec

SCOPES = ("all", "featured", "curated", "hot")

_ITEM_RETURN = (
    "{id, kind, title, group, category, detail, tags, keywords, width, height, thumb_url, image_url, "
    "curated, hot, adult, prompt}；把 image_url 传给 image_generate.images 即可作为参考图。"
)


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _filter_props(spec: ReferenceSpec) -> dict[str, Any]:
    props: dict[str, Any] = {
        "query": {"type": "string", "description": "中文关键词，空格分隔表示同时满足"},
        "group": {"type": "string", "description": f"{spec.group_label}筛选"},
    }
    if spec.category_field:
        props["category"] = {"type": "string", "description": f"{spec.category_label}筛选"}
    if spec.tag_field:
        props["tag"] = {"type": "string", "description": f"{spec.tag_label}筛选"}
    if spec.detail_field:
        props["detail"] = {"type": "string", "description": f"{spec.detail_label}筛选"}
    if spec.curated_field:
        props["scope"] = {
            "type": "string",
            "enum": list(SCOPES),
            "description": "范围：all 全部、featured 精选或热门、curated 精选、hot 热门",
        }
    if spec.adult_groups or spec.adult_categories:
        props["include_adult"] = {
            "type": "boolean",
            "description": "是否包含 18+ 分类，默认 false",
        }
    props["limit"] = {"type": "integer", "description": "返回数量，默认 20，最大 60"}
    props["offset"] = {"type": "integer", "description": "分页偏移"}
    return props


class ReferenceProvider:
    def __init__(self, spec: ReferenceSpec) -> None:
        self.library = spec
        self.spec = CapabilitySpec(
            capability_id=spec.kind,
            name=spec.name,
            description=spec.description,
            version="1.0.0",
            category="media",
            admin_path=f"/mcp-plaza/{spec.route}",
            icon=spec.icon,
            input_schema={
                f"{spec.kind}_search": {
                    "type": "object",
                    "properties": _filter_props(spec),
                },
                f"{spec.kind}_get": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "include_adult": {"type": "boolean"},
                    },
                    "required": ["id"],
                },
                f"{spec.kind}_random": {
                    "type": "object",
                    "properties": {
                        key: value for key, value in _filter_props(spec).items() if key not in {"limit", "offset"}
                    },
                },
            },
            integration={
                "rest_endpoints": [
                    {
                        "method": "POST",
                        "path": f"/v1/capabilities/{spec.kind}/search",
                        "summary": f"检索{spec.name}，返回图片直链与中文提示词",
                        "content_type": "application/json",
                    },
                    {
                        "method": "POST",
                        "path": f"/v1/capabilities/{spec.kind}/get",
                        "summary": f"按 id 获取单条{spec.name}记录",
                        "content_type": "application/json",
                    },
                    {
                        "method": "POST",
                        "path": f"/v1/capabilities/{spec.kind}/random",
                        "summary": f"随机返回一条{spec.name}记录",
                        "content_type": "application/json",
                    },
                ],
                "notes": [
                    f"先调用 {spec.kind}_search 选图，再把返回的 image_url 传给 image_generate 的 images。",
                    "图片来自公开 GitHub 仓库，按外链返回，不占用网盘空间。",
                    ATTRIBUTION,
                ],
            },
        )

    def list_mcp_tools(self) -> list[McpToolDef]:
        kind = self.library.kind
        filters = _filter_props(self.library)
        search_props = dict(filters)
        random_props = {key: value for key, value in filters.items() if key not in {"limit", "offset"}}
        return [
            McpToolDef(
                name=f"{kind}_search",
                description=f"检索{self.library.name}，返回图片直链与中文提示词。",
                input_schema={"type": "object", "properties": search_props},
                operation="search",
                returns=(
                    "{items: [" + _ITEM_RETURN + "], total, hidden_adult, limit, offset, "
                    "facets: {groups, categories, details, tags}, attribution, source, synced_at, stale}"
                ),
            ),
            McpToolDef(
                name=f"{kind}_get",
                description=f"按 id 获取单条{self.library.name}记录。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "参考图 id"},
                        "include_adult": {"type": "boolean", "description": "是否允许 18+ 记录"},
                    },
                    "required": ["id"],
                },
                operation="get",
                returns="{item: " + _ITEM_RETURN + "}",
            ),
            McpToolDef(
                name=f"{kind}_random",
                description=f"随机返回一条{self.library.name}记录。",
                input_schema={"type": "object", "properties": random_props},
                operation="random",
                returns="{item: " + _ITEM_RETURN + "}",
            ),
        ]

    async def dispatch(self, operation: str, payload: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        if ctx.mcp_key is None:
            raise ReferenceError("需要 MCP Key", status_code=401, error_type="authentication_error")
        source = get_source(self.library.kind)
        kind = self.library.kind
        query = str(payload.get("query") or payload.get("q") or "").strip()
        group = str(payload.get("group") or "").strip()
        category = str(payload.get("category") or "").strip()
        tag = str(payload.get("tag") or "").strip()
        detail = str(payload.get("detail") or "").strip()
        scope = str(payload.get("scope") or "all").strip()
        include_adult = _parse_bool(payload.get("include_adult"))
        if operation in {"search", f"{kind}_search"}:
            limit = max(1, min(_parse_int(payload.get("limit"), 20), 60))
            offset = max(0, _parse_int(payload.get("offset"), 0))
            return await asyncio.to_thread(
                source.search,
                query=query,
                group=group,
                category=category,
                tag=tag,
                detail=detail,
                scope=scope,
                include_adult=include_adult,
                limit=limit,
                offset=offset,
            )
        if operation in {"get", f"{kind}_get"}:
            reference_id = str(payload.get("id") or payload.get("reference_id") or "").strip()
            if not reference_id:
                raise ReferenceError("缺少 id", status_code=400, error_type="invalid_request")
            return await asyncio.to_thread(source.get, reference_id, include_adult=include_adult)
        if operation in {"random", f"{kind}_random"}:
            return await asyncio.to_thread(
                source.random,
                query=query,
                group=group,
                category=category,
                tag=tag,
                detail=detail,
                scope=scope,
                include_adult=include_adult,
            )
        raise ReferenceError(f"未知操作: {operation}", status_code=404, error_type="not_found")

from __future__ import annotations

from typing import Any

from app.capabilities.base import CallContext, CapabilitySpec, McpToolDef
from app.services import knowledge as knowledge_service
from app.services.knowledge import Access, KnowledgeError


class KnowledgeProvider:
    spec = CapabilitySpec(
        capability_id="knowledge",
        name="知识库",
        description="纯文本知识库：上传、分块、向量/全文/混合检索",
        version="1.1.0",
        category="rag",
        status="enabled",
        admin_path="/mcp-plaza/knowledge",
        icon="book",
        input_schema={
            "search": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "string"},
                    "kb_ids": {"type": "array", "items": {"type": "string"}},
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["vector", "fulltext", "hybrid"]},
                    "top_k": {"type": "integer"},
                },
                "required": ["query"],
            },
            "list": {"type": "object", "properties": {}},
        },
        integration={
            "rest_endpoints": [
                {
                    "method": "POST",
                    "path": "/v1/capabilities/knowledge/search",
                    "summary": "检索知识库文本块",
                    "content_type": "application/json",
                },
                {
                    "method": "GET",
                    "path": "/v1/capabilities/knowledge/bases",
                    "summary": "列出当前 Key 可见的知识库",
                },
            ],
            "notes": [
                "search 支持 mode=vector/fulltext/hybrid，top_k 默认 5；可用 kb_ids 跨库检索",
                "受限（restricted）知识库要求该 MCP Key 在库白名单内",
            ],
        },
    )

    def list_mcp_tools(self) -> list[McpToolDef]:
        return [
            McpToolDef(
                name="knowledge_list",
                description="列出当前 Key 可见的知识库（id、name、document_count）",
                input_schema={"type": "object", "properties": {}},
                operation="list",
            ),
            McpToolDef(
                name="knowledge_search",
                description="在指定知识库中检索相关文本块，可用 kb_ids 跨库检索",
                input_schema={
                    "type": "object",
                    "properties": {
                        "kb_id": {"type": "string", "description": "知识库 ID"},
                        "kb_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "多个知识库 ID（与 kb_id 二选一或并用）",
                        },
                        "query": {"type": "string", "description": "检索词"},
                        "mode": {
                            "type": "string",
                            "enum": ["vector", "fulltext", "hybrid"],
                            "default": "hybrid",
                        },
                        "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
                    },
                    "required": ["query"],
                },
                operation="search",
            ),
        ]

    def _access(self, ctx: CallContext) -> Access:
        if ctx.mcp_key is not None:
            return Access.for_key(ctx.mcp_key)
        return Access.for_system()

    async def dispatch(self, operation: str, payload: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        access = self._access(ctx)
        if operation in {"list", "knowledge_list"}:
            return {"bases": knowledge_service.list_bases(ctx.db, access)}
        if operation in {"search", "knowledge_search"}:
            kb_ids = payload.get("kb_ids")
            if isinstance(kb_ids, str):
                kb_ids = [item for item in kb_ids.split(",") if item.strip()]
            return await knowledge_service.search(
                ctx.db,
                access,
                kb_id=str(payload.get("kb_id") or "") or None,
                kb_ids=kb_ids if isinstance(kb_ids, list) else None,
                query=str(payload.get("query") or ""),
                mode=str(payload.get("mode") or "hybrid"),
                top_k=int(payload.get("top_k") or 5),
            )
        raise KnowledgeError(f"未知操作: {operation}", status_code=404, error_type="not_found")

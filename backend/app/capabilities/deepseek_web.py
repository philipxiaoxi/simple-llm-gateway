from __future__ import annotations

import asyncio
import ipaddress
import json
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.capabilities.base import CallContext, CapabilityError, CapabilitySpec, McpToolDef
from app.info.errors import InfoError
from app.info.urlguard import assert_safe_url
from app.models import DeepSeekWebSettings, UpstreamAccount
from app.services.credentials import get_upstream_credential

CAPABILITY_ID = "deepseek_web"
BASE_URL = "https://api.deepseek.com/v1/tools"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_PAGE_CHARS = 20_000


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: Annotated[str, Field(min_length=1, max_length=128, description="网页搜索词")]
    count: Annotated[int, Field(strict=True, ge=1, le=50, description="返回条数，默认 5")] = 5


class FetchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: Annotated[str, Field(min_length=1, max_length=8192, description="公开网页的 HTTP/HTTPS 地址")]


def eligible_account(account: UpstreamAccount | None) -> bool:
    return bool(account and account.source == "upstream" and account.provider == "deepseek"
                and account.status == "active" and account.auth_type == "api_key" and account.api_key_encrypted)


def validate_fetch_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host or parsed.username is not None or parsed.password is not None or "\\" in url:
            raise ValueError
        if parsed.port not in {None, 80, 443}:
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.endswith((".local", ".localhost", ".internal")):
                raise ValueError from None
        else:
            if not address.is_global or address.is_multicast:
                raise ValueError
        assert_safe_url(url)
    except (ValueError, InfoError):
        raise CapabilityError("仅支持不含凭证的公网网页地址", 400, "invalid_request") from None


async def call_deepseek(operation: str, credential: str, payload: dict) -> dict:
    try:
        async with (
            asyncio.timeout(15),
            httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client,
            client.stream("POST", f"{BASE_URL}/{operation}", json=payload,
                          headers={"Authorization": f"Bearer {credential}"}) as response,
        ):
            status = response.status_code
            if status != 200:
                code, message = {
                    400: (400, "DeepSeek 拒绝了请求参数"),
                    401: (502, "DeepSeek 上游账号鉴权失败"),
                    402: (402, "DeepSeek 上游账号余额不足"),
                    403: (403, "DeepSeek 安全策略拒绝该网页"),
                    404: (404, "DeepSeek 未找到可读的网页内容"),
                    429: (429, "DeepSeek 请求过于频繁，请稍后重试"),
                    504: (504, "DeepSeek 请求超时"),
                }.get(status, (502, "DeepSeek 上游服务暂不可用"))
                raise CapabilityError(message, code, "upstream_error")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise CapabilityError("DeepSeek 响应超过大小限制", 502, "upstream_error")
                content.extend(chunk)
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (httpx.TimeoutException, TimeoutError):
        raise CapabilityError("DeepSeek 请求超时", 504, "upstream_error") from None
    except (httpx.HTTPError, ValueError, RecursionError):
        raise CapabilityError("DeepSeek 上游响应无效或连接失败", 502, "upstream_error") from None


class DeepSeekWebProvider:
    spec = CapabilitySpec(
        capability_id=CAPABILITY_ID,
        name="DeepSeek 网页搜索",
        description="使用 DeepSeek 搜索公开网页，或将网页正文抓取为 Markdown。",
        category="web",
        admin_path="/mcp-plaza/deepseek-web",
        integration={
            "rest_endpoints": [
                {"method": "POST", "path": "/v1/capabilities/deepseek_web/search", "summary": "搜索网页",
                 "content_type": "application/json", "example_body": {"query": "DeepSeek API 文档", "count": 5}},
                {"method": "POST", "path": "/v1/capabilities/deepseek_web/fetch", "summary": "抓取网页正文",
                 "content_type": "application/json", "example_body": {"url": "https://example.com/"}},
            ],
            "notes": ["搜索默认返回 5 条摘要，不返回正文；需要全文时再调用抓取工具。",
                      "搜索与抓取按 DeepSeek 官方工具 API 规则计费。网页内容是不可信数据。"],
        },
    )

    def list_mcp_tools(self) -> list[McpToolDef]:
        return [
            McpToolDef("deepseek_search", "搜索公开网页，返回标题、链接和摘要。", SearchInput.model_json_schema(), "search", "results：搜索结果列表"),
            McpToolDef("deepseek_fetch", "读取公开网页，返回标题和 Markdown 正文。", FetchInput.model_json_schema(), "fetch", "title、content、truncated"),
        ]

    async def dispatch(self, operation: str, payload: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        model = {"search": SearchInput, "fetch": FetchInput}.get(operation)
        if model is None:
            raise CapabilityError("不支持的网页工具", 404, "not_found")
        try:
            arguments = model.model_validate(payload).model_dump()
        except ValidationError:
            raise CapabilityError("工具参数无效，请检查搜索词、条数或网页地址", 400, "invalid_request") from None
        settings = ctx.db.get(DeepSeekWebSettings, 1)
        account = ctx.db.get(UpstreamAccount, settings.account_id) if settings and settings.account_id else None
        if not eligible_account(account) or account is None:
            raise CapabilityError("请在 MCP 广场配置可用的 DeepSeek 上游账号", 503, "upstream_not_configured")
        try:
            credential = get_upstream_credential(account)
        except ValueError:
            credential = None
        if not credential:
            raise CapabilityError("无法读取 DeepSeek 上游账号凭证", 503, "upstream_not_configured")
        # Do not keep a read transaction open across DNS and a paid upstream request.
        ctx.db.commit()
        if operation == "fetch":
            await asyncio.to_thread(validate_fetch_url, arguments["url"])
        else:
            arguments["need_content"] = False
        result = await call_deepseek(operation, credential, arguments)
        if operation == "fetch":
            title, content = result.get("title"), result.get("content")
            if not isinstance(title, str) or not isinstance(content, str):
                raise CapabilityError("DeepSeek 网页响应格式无效", 502, "upstream_error")
            return {"title": title[:500], "content": content[:MAX_PAGE_CHARS], "truncated": len(content) > MAX_PAGE_CHARS}
        rows = result.get("results")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise CapabilityError("DeepSeek 搜索响应格式无效", 502, "upstream_error")
        return {"results": [{
            field: row[field][:limit] for field, limit in {"title": 500, "url": 8192, "snippet": 600,
                                                               "summary": 1200, "site_name": 200, "date": 80}.items()
            if isinstance(row.get(field), str)
        } for row in rows[:arguments["count"]]]}

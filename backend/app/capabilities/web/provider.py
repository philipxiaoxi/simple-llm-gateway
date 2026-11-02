from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.capabilities.base import CallContext, CapabilitySpec, McpToolDef
from app.capabilities.docparse.convert import convert_bytes
from app.capabilities.docparse.errors import DocParseError
from app.config import get_settings

from . import robots
from .cache import TTLCache
from .errors import WebError
from .extract import html_to_markdown
from .fetch import FetchResult, fetch
from .search import search as run_search

HTML_TYPES = {"text/html", "application/xhtml+xml", "application/xml", "text/xml"}
TEXT_TYPES = {"text/plain", "text/markdown", "text/x-markdown"}
PDF_TYPES = {"application/pdf"}
OFFICE_TYPES = {
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}
DOC_SUFFIXES = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}

_FETCH_CACHE: TTLCache[dict[str, Any]] = TTLCache(max_entries=64)


def _clamp(value: Any, *, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _document_name(url: str, content_type: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in DOC_SUFFIXES:
        return f"page{suffix}"
    return f"page{OFFICE_TYPES.get(content_type, '.bin')}"


def _decode(result: FetchResult) -> str:
    encoding = result.encoding or "utf-8"
    try:
        return result.body.decode(encoding, errors="replace")
    except LookupError:
        return result.body.decode("utf-8", errors="replace")


class WebProvider:
    spec = CapabilitySpec(
        capability_id="web",
        name="联网搜索与网页解析",
        description="抓取网页正文为 Markdown，并在本地资讯库与全网检索",
        version="1.0.0",
        category="web",
        status="enabled",
        admin_path="",
        icon="globe",
        input_schema={
            "search": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer"},
                    "time_range": {"type": "string"},
                    "site": {"type": "string"},
                    "scope": {"type": "string"},
                },
                "required": ["query"],
            },
            "fetch": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer"},
                    "include_links": {"type": "boolean"},
                },
                "required": ["url"],
            },
        },
        integration={
            "rest_endpoints": [
                {
                    "method": "POST",
                    "path": "/v1/capabilities/web/search",
                    "summary": "在本地资讯库与全网检索网页",
                },
                {
                    "method": "POST",
                    "path": "/v1/capabilities/web/fetch",
                    "summary": "抓取网页并提取正文为 Markdown",
                },
            ],
            "notes": [
                "只支持 http/https，拒绝内网与保留地址",
                "遵守 robots.txt；PDF 与 Office 文档会转交文档解析管线",
                "不执行 JavaScript，JS 渲染站点可能提取不到正文",
            ],
        },
    )

    def list_mcp_tools(self) -> list[McpToolDef]:
        return [
            McpToolDef(
                name="web_search",
                description="检索网页。默认先查本地资讯库，结果不足时补充联网搜索。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "查询词"},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
                        "time_range": {
                            "type": "string",
                            "enum": ["day", "week", "month", "year", "any"],
                            "default": "any",
                        },
                        "site": {"type": "string", "description": "只在这些域名内检索，逗号分隔"},
                        "scope": {
                            "type": "string",
                            "enum": ["auto", "local", "web"],
                            "default": "auto",
                            "description": "auto 本地优先；local 只查本地；web 只查联网",
                        },
                    },
                    "required": ["query"],
                },
                operation="search",
                returns="results[]: {title, url, snippet, source, published_at, score, provider}；providers[]；warnings[]",
            ),
            McpToolDef(
                name="web_fetch",
                description="抓取一个网页并提取正文，返回干净 Markdown。支持文章、文档与 PDF。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "http/https 链接"},
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1000,
                            "maximum": 200000,
                            "description": "正文最大字符数，默认取服务配置",
                        },
                        "include_links": {
                            "type": "boolean",
                            "description": "是否在 Markdown 中保留链接，默认 false",
                        },
                    },
                    "required": ["url"],
                },
                operation="fetch",
                returns="url, final_url, title, author, published_at, markdown, chars, truncated, extract_fallback, warnings[]",
            )
        ]

    async def dispatch(self, operation: str, payload: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        if ctx.mcp_key is None:
            raise WebError("需要 MCP Key", status_code=401, error_type="authentication_error")
        if operation in {"search", "web_search"}:
            return await self._search(ctx.db, payload)
        if operation in {"fetch", "web_fetch"}:
            return await self._fetch(payload)
        raise WebError(f"未知操作: {operation}", status_code=404, error_type="not_found")

    async def _search(self, db, payload: dict[str, Any]) -> dict[str, Any]:
        top_k = _clamp(payload.get("top_k"), low=1, high=20, default=8)
        time_range = str(payload.get("time_range") or "any").lower()
        if time_range not in {"day", "week", "month", "year", "any"}:
            raise WebError("time_range 只能是 day/week/month/year/any")
        scope = str(payload.get("scope") or "auto").lower()
        return await run_search(
            db,
            query=str(payload.get("query") or ""),
            top_k=top_k,
            time_range=time_range,
            site=str(payload.get("site") or ""),
            scope=scope,
        )

    async def _fetch(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        url = str(payload.get("url") or "").strip()
        if not url:
            raise WebError("url 必填")
        if not url.lower().startswith(("http://", "https://")):
            raise WebError("只支持 http/https 链接", error_type="blocked_host")
        include_links = bool(payload.get("include_links"))
        max_chars = _clamp(
            payload.get("max_chars"),
            low=1000,
            high=200000,
            default=settings.web_fetch_max_chars,
        )

        cache_key = f"{url}|{include_links}|{max_chars}"
        cached = _FETCH_CACHE.get(cache_key)
        if cached is not None:
            return cached

        if not await robots.allowed(url):
            raise WebError("目标站点 robots.txt 禁止抓取", status_code=403, error_type="robots_disallowed")

        result = await fetch(url)
        warnings: list[str] = []
        content_type = result.content_type
        body = result.body

        if content_type in PDF_TYPES or content_type in OFFICE_TYPES:
            try:
                converted = convert_bytes(_document_name(result.final_url, content_type), body)
            except DocParseError as error:
                raise WebError(error.message, status_code=error.status_code, error_type=error.error_type) from error
            markdown = converted.markdown
            warnings.extend(converted.warnings)
            title = Path(result.final_url).name
            author = ""
            published_at = None
            site_name = ""
            fallback = False
        elif content_type in HTML_TYPES or not content_type or content_type == "application/octet-stream":
            extracted = html_to_markdown(body, result.final_url, include_links=include_links)
            markdown = extracted.markdown
            title = extracted.title
            author = extracted.author
            published_at = extracted.published_at
            site_name = extracted.site_name
            fallback = extracted.fallback
            if fallback:
                warnings.append("正文提取降级为整页清洗，可能包含少量非正文内容")
        elif content_type in TEXT_TYPES:
            markdown = _decode(result).strip()
            if not markdown:
                raise WebError("页面内容为空", status_code=422, error_type="empty_content")
            title = Path(urlparse(result.final_url).path).name
            author = ""
            published_at = None
            site_name = ""
            fallback = False
        else:
            raise WebError(
                f"不支持的内容类型: {content_type}",
                status_code=422,
                error_type="unsupported_content_type",
            )

        truncated = len(markdown) > max_chars
        if truncated:
            markdown = markdown[:max_chars]
            warnings.append(f"正文超过 {max_chars} 字符，已截断")

        response = {
            "url": url,
            "final_url": result.final_url,
            "status": result.status,
            "content_type": content_type,
            "title": title,
            "author": author,
            "published_at": published_at,
            "site_name": site_name,
            "markdown": markdown,
            "chars": len(markdown),
            "truncated": truncated,
            "extract_fallback": fallback,
            "warnings": warnings,
        }
        _FETCH_CACHE.set(cache_key, response, settings.web_fetch_cache_ttl_seconds)
        return response


def clear_cache() -> None:
    _FETCH_CACHE.clear()
    robots.clear_cache()

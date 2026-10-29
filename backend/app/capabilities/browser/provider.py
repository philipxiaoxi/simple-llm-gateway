from __future__ import annotations

from typing import Any

from app.capabilities.base import CallContext, CapabilitySpec, McpToolDef
from app.capabilities.browser import manager, screenshots
from app.capabilities.browser.errors import BrowserError
from app.capabilities.browser.policy import assert_navigable_url
from app.config import get_settings
from app.models import BrowserSession

_SESSION_FIELD = {"type": "string", "description": "browser_open 返回的 session_id"}
_TARGET_FIELDS = {
    "ref": {"type": "string", "description": "快照中的元素引用，如 e3"},
    "selector": {"type": "string", "description": "CSS 选择器（与 ref 二选一）"},
}


class BrowserProvider:
    spec = CapabilitySpec(
        capability_id="browser",
        name="在线浏览器",
        description="AI 远程控制无头浏览器：导航、快照、点击、输入与截图，截图以链接回传",
        version="1.0.0",
        category="tools",
        status="enabled",
        admin_path="/mcp-plaza/browser",
        icon="globe",
        input_schema={
            "open": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                },
                "required": ["url"],
            }
        },
        integration={
            "rest_endpoints": [
                {"method": "POST", "path": "/v1/browser/sessions", "summary": "创建浏览器会话并打开页面"},
                {"method": "GET", "path": "/v1/browser/sessions", "summary": "列出当前 Key 的会话"},
                {"method": "GET", "path": "/v1/browser/sessions/{session_id}", "summary": "会话详情"},
                {"method": "DELETE", "path": "/v1/browser/sessions/{session_id}", "summary": "关闭会话"},
                {
                    "method": "POST",
                    "path": "/v1/browser/sessions/{session_id}/actions/{action}",
                    "summary": "执行导航/点击/输入/截图等动作",
                },
                {"method": "GET", "path": "/v1/browser/screenshots/{id}", "summary": "查看截图（签名令牌）"},
            ],
            "notes": [
                "先用 browser_open 或 POST /v1/browser/sessions 打开页面，拿到 session_id；后续动作都带 session_id",
                "快照中的 [ref=eN] 可直接用于 click/type/select；也可以传 CSS selector",
                "screenshot_url 为相对路径 /v1/browser/screenshots/{id}?token=...，拼接部署地址即可访问；absolute_url 为完整地址",
                "默认禁止私网/回环/云元数据地址；能力面不提供任意 JavaScript 求值、文件上传与下载",
                "空闲会话会被自动回收，可通过 browser_close 主动关闭",
            ],
        },
    )

    def list_mcp_tools(self) -> list[McpToolDef]:
        return [
            McpToolDef(
                name="browser_open",
                description="创建浏览器会话并打开 URL，返回 session_id、标题、页面快照与元素引用。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要打开的 http/https 地址"},
                        "width": {"type": "integer", "description": "视口宽度"},
                        "height": {"type": "integer", "description": "视口高度"},
                    },
                    "required": ["url"],
                },
                operation="open",
            ),
            McpToolDef(
                name="browser_navigate",
                description="在已有会话中导航到新地址，返回最新快照。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "session_id": _SESSION_FIELD,
                        "url": {"type": "string"},
                        "wait_until": {"type": "string", "enum": ["load", "domcontentloaded", "networkidle"]},
                    },
                    "required": ["session_id", "url"],
                },
                operation="navigate",
            ),
            McpToolDef(
                name="browser_snapshot",
                description="读取当前页面快照与可交互元素引用。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "session_id": _SESSION_FIELD,
                        "max_chars": {"type": "integer"},
                    },
                    "required": ["session_id"],
                },
                operation="snapshot",
            ),
            McpToolDef(
                name="browser_screenshot",
                description="截取当前页面并返回可访问的截图链接。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "session_id": _SESSION_FIELD,
                        "full_page": {"type": "boolean"},
                        "selector": {"type": "string"},
                        "format": {"type": "string", "enum": ["png", "jpeg"]},
                    },
                    "required": ["session_id"],
                },
                operation="screenshot",
            ),
            McpToolDef(
                name="browser_click",
                description="点击快照中的元素（ref）或 CSS 选择器，返回操作后的快照。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "session_id": _SESSION_FIELD,
                        **_TARGET_FIELDS,
                        "button": {"type": "string", "enum": ["left", "right", "middle"]},
                        "double": {"type": "boolean"},
                    },
                    "required": ["session_id"],
                },
                operation="click",
            ),
            McpToolDef(
                name="browser_type",
                description="向输入框填写文本；submit=true 时按回车提交。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "session_id": _SESSION_FIELD,
                        **_TARGET_FIELDS,
                        "text": {"type": "string"},
                        "submit": {"type": "boolean"},
                    },
                    "required": ["session_id", "text"],
                },
                operation="type",
            ),
            McpToolDef(
                name="browser_press_key",
                description="向当前焦点发送按键，如 Enter、Escape、Tab。",
                input_schema={
                    "type": "object",
                    "properties": {"session_id": _SESSION_FIELD, "key": {"type": "string"}},
                    "required": ["session_id", "key"],
                },
                operation="press_key",
            ),
            McpToolDef(
                name="browser_select_option",
                description="在下拉框选择指定值。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "session_id": _SESSION_FIELD,
                        **_TARGET_FIELDS,
                        "values": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["session_id", "values"],
                },
                operation="select_option",
            ),
            McpToolDef(
                name="browser_scroll",
                description="滚动页面，direction 为 up/down，delta 为像素量。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "session_id": _SESSION_FIELD,
                        "direction": {"type": "string", "enum": ["up", "down"]},
                        "delta": {"type": "integer"},
                    },
                    "required": ["session_id"],
                },
                operation="scroll",
            ),
            McpToolDef(
                name="browser_wait",
                description="等待指定毫秒、元素出现或文本出现。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "session_id": _SESSION_FIELD,
                        "ms": {"type": "integer"},
                        "selector": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["session_id"],
                },
                operation="wait",
            ),
            McpToolDef(
                name="browser_back",
                description="后退到上一页并返回快照。",
                input_schema={"type": "object", "properties": {"session_id": _SESSION_FIELD}, "required": ["session_id"]},
                operation="back",
            ),
            McpToolDef(
                name="browser_forward",
                description="前进到下一页并返回快照。",
                input_schema={"type": "object", "properties": {"session_id": _SESSION_FIELD}, "required": ["session_id"]},
                operation="forward",
            ),
            McpToolDef(
                name="browser_list_sessions",
                description="列出当前 MCP Key 的浏览器会话。",
                input_schema={"type": "object", "properties": {}},
                operation="list",
            ),
            McpToolDef(
                name="browser_close",
                description="关闭浏览器会话并释放资源。",
                input_schema={"type": "object", "properties": {"session_id": _SESSION_FIELD}, "required": ["session_id"]},
                operation="close",
            ),
        ]

    async def dispatch(self, operation: str, payload: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        if ctx.mcp_key is None:
            raise BrowserError("需要 MCP Key", status_code=401, error_type="authentication_error")
        key_id = ctx.mcp_key.id
        op = operation.removeprefix("browser_")
        if op == "open":
            return await self._open(ctx, payload, key_id)
        if op == "list":
            rows = manager.list_sessions(ctx.db, mcp_key_id=key_id)
            return {"items": [manager.session_payload(row) for row in rows]}
        if op == "close":
            await manager.close_session(ctx.db, self._session_id(payload), key_id)
            return {"closed": True, "session_id": self._session_id(payload)}
        if op == "status":
            session_id = self._session_id(payload)
            row = ctx.db.get(BrowserSession, session_id)
            if row is None or (key_id is not None and row.mcp_key_id != key_id):
                raise BrowserError("会话不存在", status_code=404, error_type="not_found")
            return manager.session_payload(
                row, screenshot=screenshots.latest_screenshot(ctx.db, row.id)
            )
        return await self._action(ctx, op, payload, key_id)

    async def _open(self, ctx: CallContext, payload: dict[str, Any], key_id: int) -> dict[str, Any]:
        settings = get_settings()
        url = str(payload.get("url") or "").strip()
        assert_navigable_url(
            url,
            allowed_hosts=settings.browser_allowed_hosts,
            blocked_hosts=settings.browser_blocked_hosts,
        )
        row, _state = await manager.open_session(
            ctx.db,
            mcp_key_id=key_id,
            url=url,
            width=_int(payload.get("width")),
            height=_int(payload.get("height")),
        )
        page = manager.live_session(row.id)
        assert page is not None
        return await self._snapshot_result(ctx, row, page, include_screenshot=bool(payload.get("include_screenshot")))

    async def _action(self, ctx: CallContext, op: str, payload: dict[str, Any], key_id: int) -> dict[str, Any]:
        session_id = self._session_id(payload)
        row, page = manager.require_page(ctx.db, session_id, key_id)
        settings = get_settings()

        if op == "navigate":
            url = str(payload.get("url") or "").strip()
            assert_navigable_url(
                url,
                allowed_hosts=settings.browser_allowed_hosts,
                blocked_hosts=settings.browser_blocked_hosts,
            )
            state = await page.goto(
                url,
                wait_until=_str(payload.get("wait_until")),
                timeout_ms=_int(payload.get("timeout_ms")),
            )
            manager.touch(ctx.db, row, state)
            return await self._snapshot_result(ctx, row, page, include_screenshot=bool(payload.get("include_screenshot")))
        if op == "snapshot":
            return await self._snapshot_result(
                ctx,
                row,
                page,
                max_chars=_int(payload.get("max_chars")) or 6000,
                include_screenshot=bool(payload.get("include_screenshot")),
            )
        if op == "screenshot":
            data, content_type, width, height = await page.screenshot(
                full_page=bool(payload.get("full_page")),
                selector=_str(payload.get("selector")),
                fmt=_str(payload.get("format")) or "png",
            )
            shot = screenshots.save_screenshot(
                ctx.db, row, data=data, content_type=content_type, width=width, height=height
            )
            manager.touch(ctx.db, row)
            return {"session_id": row.id, **shot}
        if op == "click":
            await page.click(
                ref=_str(payload.get("ref")),
                selector=_str(payload.get("selector")),
                button=_str(payload.get("button")) or "left",
                double=bool(payload.get("double")),
            )
        elif op == "type":
            await page.type_text(
                ref=_str(payload.get("ref")),
                selector=_str(payload.get("selector")),
                text=str(payload.get("text") or ""),
                submit=bool(payload.get("submit")),
            )
        elif op == "press_key":
            await page.press(key=str(payload.get("key") or "Enter"))
        elif op == "select_option":
            values = payload.get("values")
            if not isinstance(values, list) or not values:
                raise BrowserError("values 不能为空")
            await page.select(
                ref=_str(payload.get("ref")),
                selector=_str(payload.get("selector")),
                values=[str(item) for item in values],
            )
        elif op == "scroll":
            await page.scroll(
                direction=_str(payload.get("direction")) or "down",
                delta=_int(payload.get("delta")),
            )
        elif op == "wait":
            await page.wait(
                ms=_int(payload.get("ms")),
                selector=_str(payload.get("selector")),
                text=_str(payload.get("text")),
                timeout_ms=_int(payload.get("timeout_ms")),
            )
        elif op == "back":
            state = await page.back()
            manager.touch(ctx.db, row, state)
            return await self._snapshot_result(ctx, row, page)
        elif op == "forward":
            state = await page.forward()
            manager.touch(ctx.db, row, state)
            return await self._snapshot_result(ctx, row, page)
        else:
            raise BrowserError(f"未知操作: {op}", status_code=404, error_type="not_found")

        state = await page.state()
        manager.touch(ctx.db, row, state)
        return await self._snapshot_result(ctx, row, page, include_screenshot=bool(payload.get("include_screenshot")))

    async def _snapshot_result(
        self,
        ctx: CallContext,
        row,
        page,
        *,
        max_chars: int = 6000,
        include_screenshot: bool = False,
    ) -> dict[str, Any]:
        snap = await page.snapshot(max_chars=max_chars)
        result: dict[str, Any] = {
            "session_id": row.id,
            "url": snap.get("url") or row.current_url,
            "title": snap.get("title") or row.title,
            "snapshot": snap.get("snapshot") or "",
            "refs": snap.get("refs") or [],
        }
        manager.touch(ctx.db, row, {"url": result["url"], "title": result["title"]})
        if include_screenshot:
            data, content_type, width, height = await page.screenshot()
            result.update(
                screenshots.save_screenshot(
                    ctx.db, row, data=data, content_type=content_type, width=width, height=height
                )
            )
        return result

    @staticmethod
    def _session_id(payload: dict[str, Any]) -> str:
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            raise BrowserError("session_id 不能为空")
        return session_id


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

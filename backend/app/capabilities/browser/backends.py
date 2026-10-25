"""浏览器后端抽象。

v1 使用 LocalPlaywrightBackend（同进程 Chromium）；后续可增加 RemoteBrowserBackend，
Provider 与 Manager 不感知具体实现。
"""

from __future__ import annotations

import struct
from typing import Any, Protocol

from app.capabilities.browser.errors import BrowserError
from app.config import get_settings

_SNAPSHOT_JS = """
() => {
  const selector = 'a,button,input,textarea,select,[role=button],[role=link],[contenteditable=true]';
  const nodes = Array.from(document.querySelectorAll(selector));
  const rows = [];
  let index = 0;
  for (const el of nodes) {
    if (!el || el.disabled) continue;
    const style = window.getComputedStyle(el);
    if (style && (style.display === 'none' || style.visibility === 'hidden')) continue;
    index += 1;
    const ref = 'e' + index;
    el.setAttribute('data-mcp-ref', ref);
    const label = (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('placeholder') || '')
      .trim().replace(/\\s+/g, ' ').slice(0, 80);
    const role = el.getAttribute('role') || el.tagName.toLowerCase();
    rows.push({ ref, role, label });
  }
  return {
    url: location.href,
    title: document.title,
    rows,
    text: document.body ? document.body.innerText.slice(0, 6000) : '',
  };
}
"""


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    return 0, 0


class PageSession(Protocol):
    async def goto(self, url: str, *, wait_until: str | None = None, timeout_ms: int | None = None) -> dict[str, Any]: ...
    async def snapshot(self, *, max_chars: int = 6000) -> dict[str, Any]: ...
    async def screenshot(
        self, *, full_page: bool = False, selector: str | None = None, fmt: str = "png"
    ) -> tuple[bytes, str, int, int]: ...
    async def click(self, *, ref: str | None, selector: str | None, button: str, double: bool) -> None: ...
    async def type_text(
        self, *, ref: str | None, selector: str | None, text: str, submit: bool
    ) -> None: ...
    async def press(self, *, key: str) -> None: ...
    async def select(self, *, ref: str | None, selector: str | None, values: list[str]) -> None: ...
    async def scroll(self, *, direction: str, delta: int | None) -> None: ...
    async def wait(
        self, *, ms: int | None, selector: str | None, text: str | None, timeout_ms: int | None
    ) -> None: ...
    async def back(self) -> dict[str, Any]: ...
    async def forward(self) -> dict[str, Any]: ...
    async def state(self) -> dict[str, Any]: ...
    async def close(self) -> None: ...


class BrowserBackend(Protocol):
    name: str

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def new_session(self, *, width: int, height: int, user_agent: str) -> PageSession: ...


class LocalPageSession:
    def __init__(self, context: Any, page: Any, timeout_ms: int) -> None:
        self._context = context
        self._page = page
        self._timeout = max(1000, int(timeout_ms))

    async def state(self) -> dict[str, Any]:
        return {"url": self._page.url, "title": await self._page.title()}

    async def goto(
        self, url: str, *, wait_until: str | None = None, timeout_ms: int | None = None
    ) -> dict[str, Any]:
        await self._page.goto(
            url,
            wait_until=wait_until or "domcontentloaded",
            timeout=timeout_ms or self._timeout,
        )
        return await self.state()

    async def snapshot(self, *, max_chars: int = 6000) -> dict[str, Any]:
        data = await self._page.evaluate(_SNAPSHOT_JS)
        rows = data.get("rows") or []
        lines = [f"URL: {data.get('url', '')}", f"Title: {data.get('title', '')}", "", "可交互元素："]
        for row in rows:
            label = f' "{row["label"]}"' if row.get("label") else ""
            lines.append(f'[ref={row["ref"]}] {row["role"]}{label}')
        lines.extend(["", "页面文本：", data.get("text", "")])
        text = "\n".join(lines)
        limit = max(500, int(max_chars))
        if len(text) > limit:
            text = text[:limit] + "\n…（已截断）"
        return {
            "url": data.get("url", ""),
            "title": data.get("title", ""),
            "snapshot": text,
            "refs": rows,
        }

    def _target(self, ref: str | None, selector: str | None):
        if ref:
            return self._page.locator(f'[data-mcp-ref="{ref}"]')
        if selector:
            return self._page.locator(selector)
        raise BrowserError("需要提供 ref 或 selector")

    async def click(
        self, *, ref: str | None, selector: str | None, button: str, double: bool
    ) -> None:
        await self._target(ref, selector).click(
            button=button or "left", click_count=2 if double else 1, timeout=self._timeout
        )

    async def type_text(
        self, *, ref: str | None, selector: str | None, text: str, submit: bool
    ) -> None:
        target = self._target(ref, selector)
        await target.fill(text or "", timeout=self._timeout)
        if submit:
            await target.press("Enter", timeout=self._timeout)

    async def press(self, *, key: str) -> None:
        await self._page.keyboard.press(key)

    async def select(self, *, ref: str | None, selector: str | None, values: list[str]) -> None:
        await self._target(ref, selector).select_option(values, timeout=self._timeout)

    async def scroll(self, *, direction: str, delta: int | None) -> None:
        amount = abs(int(delta)) if delta else 600
        if direction == "up":
            amount = -amount
        await self._page.mouse.wheel(0, amount)

    async def wait(
        self,
        *,
        ms: int | None,
        selector: str | None,
        text: str | None,
        timeout_ms: int | None,
    ) -> None:
        timeout = timeout_ms or self._timeout
        if selector:
            await self._page.wait_for_selector(selector, timeout=timeout)
            return
        if text:
            await self._page.get_by_text(text).first.wait_for(timeout=timeout)
            return
        await self._page.wait_for_timeout(max(0, int(ms or 500)))

    async def back(self) -> dict[str, Any]:
        await self._page.go_back(wait_until="domcontentloaded", timeout=self._timeout)
        return await self.state()

    async def forward(self) -> dict[str, Any]:
        await self._page.go_forward(wait_until="domcontentloaded", timeout=self._timeout)
        return await self.state()

    async def screenshot(
        self, *, full_page: bool = False, selector: str | None = None, fmt: str = "png"
    ) -> tuple[bytes, str, int, int]:
        image_type = "jpeg" if str(fmt).lower() in {"jpg", "jpeg"} else "png"
        data: bytes
        if selector:
            data = await self._page.locator(selector).screenshot(type=image_type, timeout=self._timeout)
        else:
            data = await self._page.screenshot(full_page=full_page, type=image_type, timeout=self._timeout)
        width, height = _png_size(data) if image_type == "png" else (0, 0)
        return data, f"image/{image_type}", width, height

    async def close(self) -> None:
        await self._context.close()


class LocalPlaywrightBackend:
    name = "local"

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None

    async def start(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:  # pragma: no cover - 仅缺依赖时触发
            raise BrowserError(
                "浏览器引擎未安装", status_code=503, error_type="browser_unavailable"
            ) from error
        settings = get_settings()
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(
                headless=bool(settings.browser_headless),
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
        except Exception as error:  # noqa: BLE001
            await self._playwright.stop()
            self._playwright = None
            raise BrowserError(
                "浏览器启动失败", status_code=503, error_type="browser_unavailable"
            ) from error

    async def new_session(self, *, width: int, height: int, user_agent: str) -> PageSession:
        await self.start()
        assert self._browser is not None
        context = await self._browser.new_context(
            viewport={"width": max(320, int(width)), "height": max(240, int(height))},
            user_agent=user_agent,
            accept_downloads=False,
        )
        page = await context.new_page()
        return LocalPageSession(context, page, get_settings().browser_nav_timeout_ms)

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

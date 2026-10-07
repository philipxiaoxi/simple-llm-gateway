"""微信公众号正文 HTML 的清洗。

上游 `content_noencode` 是文章原始 HTML，含内联样式与图片；直接渲染有 XSS 风险，
因此入库前用白名单重建：

- 丢弃 `script` / `style` / `iframe` 等危险标签及其内容；
- 标签与属性都按白名单保留（保留 `style` 以还原公众号排版）；
- 剔除 `on*` 事件、`javascript:` / `vbscript:` / `data:text/html` 链接；
- 其它不认识的标签只丢标签、保留文字。

图片在序列化时再改写成平台本地媒体地址（见 `items._rewrite_content_images`）。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_ALLOWED_TAGS = {
    "p", "br", "div", "section", "article", "span", "center",
    "strong", "b", "em", "i", "u", "s", "sub", "sup", "font", "mark",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "ul", "ol", "li",
    "img", "figure", "figcaption",
    "a", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
    "hr", "code", "pre",
}
_VOID_TAGS = {"br", "img", "hr", "col", "wbr"}
_DROP_TAGS = {
    "script", "style", "iframe", "object", "embed", "link", "meta",
    "form", "input", "button", "textarea", "select", "option", "svg", "math",
    "noscript", "template", "head", "title", "base", "applet", "frame", "frameset",
}
_ALLOWED_ATTRS = {
    "src", "alt", "width", "height", "style", "class",
    "colspan", "rowspan", "align", "valign", "href", "title", "data-src",
}
_BAD_URL_PREFIXES = ("javascript:", "vbscript:", "data:text/html")
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


def _clean_attrs(attrs: list[tuple[str, str | None]]) -> str:
    parts: list[str] = []
    for name, value in attrs:
        name = (name or "").lower()
        if name not in _ALLOWED_ATTRS:
            continue
        value = value if value is not None else ""
        lowered = value.strip().lower()
        if name in {"src", "href"} and lowered.startswith(_BAD_URL_PREFIXES):
            continue
        if name == "style" and ("javascript:" in lowered or "expression(" in lowered):
            continue
        parts.append(f'{name}="{value.replace(chr(34), "&quot;")}"')
    return (" " + " ".join(parts)) if parts else ""


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._skip_depth = 0

    @property
    def html(self) -> str:
        return "".join(self._out)

    def _render_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        rendered = _clean_attrs(attrs)
        # 正文里的链接统一新标签打开，避免在单页应用内跳走
        if tag == "a":
            rendered += ' target="_blank" rel="noopener noreferrer"'
        return rendered

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth or tag not in _ALLOWED_TAGS:
            return
        self._out.append(f"<{tag}{self._render_attrs(tag, attrs)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth or tag in _DROP_TAGS or tag not in _ALLOWED_TAGS:
            return
        self._out.append(f"<{tag}{self._render_attrs(tag, attrs)}/>")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth or tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        self._out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._out.append(data.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def sanitize_wechat_html(html: str) -> str:
    if not html:
        return ""
    cleaner = _Cleaner()
    try:
        cleaner.feed(html)
        cleaner.close()
    except Exception:  # noqa: BLE001 - 解析异常时退回空串，详情页展示纯文本
        return ""
    return cleaner.html


def sanitize_feed_html(html: str) -> str:
    """通用正文 HTML 清洗（RSS/Atom 等）。

    与公众号共用同一套白名单。RSS 正文里的外链图片不会被转存（媒体只允许
    Telegram/微信图床），直连上游又会泄漏访问，这里把 `<img>` 一并去掉。
    """
    cleaned = sanitize_wechat_html(html)
    if not cleaned:
        return ""
    return _IMG_TAG.sub("", cleaned)

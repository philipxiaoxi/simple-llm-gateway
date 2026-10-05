"""把公众号正文的内联样式适配到站点深色主题。

公众号 HTML 带大量内联样式，常见「黑字白底」在深色站点里会变成黑字黑底、看不清。
这里在渲染前扫描 `style="..."`：

- `color` 偏暗（感知亮度 < 0.5）→ 转成同色相的浅色（保持强调色，只提亮）；
- `background` / `background-color` 偏亮（> 0.6）→ 直接去掉，露出站点深色底；
- 无法解析的颜色（渐变 / 图片 / 未知关键字）保持原样。

只处理我们清洗后的 HTML（属性用双引号、值里的引号已转义），正则安全。
"""

from __future__ import annotations

import re

_STYLE_ATTR = re.compile(r'style="([^"]*)"', re.IGNORECASE)
_HEX = re.compile(r"^#([0-9a-f]{3,8})$")
_RGB = re.compile(r"rgba?\(([^)]*)\)")
_HSL = re.compile(r"hsla?\(([^)]*)\)")

# 常见的 CSS 命名色（WeChat 正文里高频出现的一部分）
_NAMED = {
    "black": (0, 0, 0, 1.0),
    "white": (255, 255, 255, 1.0),
    "red": (255, 0, 0, 1.0),
    "darkred": (139, 0, 0, 1.0),
    "blue": (0, 0, 255, 1.0),
    "darkblue": (0, 0, 139, 1.0),
    "green": (0, 128, 0, 1.0),
    "darkgreen": (0, 100, 0, 1.0),
    "orange": (255, 165, 0, 1.0),
    "purple": (128, 0, 128, 1.0),
    "navy": (0, 0, 128, 1.0),
    "teal": (0, 128, 128, 1.0),
    "maroon": (128, 0, 0, 1.0),
    "gray": (128, 128, 128, 1.0),
    "grey": (128, 128, 128, 1.0),
    "darkgray": (169, 169, 169, 1.0),
    "darkgrey": (169, 169, 169, 1.0),
    "dimgray": (105, 105, 105, 1.0),
    "dimgrey": (105, 105, 105, 1.0),
    "silver": (192, 192, 192, 1.0),
    "lightgray": (211, 211, 211, 1.0),
    "lightgrey": (211, 211, 211, 1.0),
    "gainsboro": (220, 220, 220, 1.0),
    "whitesmoke": (245, 245, 245, 1.0),
    "darkgoldenrod": (184, 134, 11, 1.0),
    "goldenrod": (218, 165, 32, 1.0),
    "darkorange": (255, 140, 0, 1.0),
}

_DARK_TEXT_LUMINANCE = 0.5
_LIGHT_BG_LUMINANCE = 0.6
_MIN_LIGHTNESS = 0.8


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _parse_color(value: str) -> tuple[int, int, int, float] | None:
    text = value.strip().lower()
    if not text or text in {"transparent", "inherit", "currentcolor", "initial", "unset", "none"}:
        return None

    hex_match = _HEX.match(text)
    if hex_match:
        digits = hex_match.group(1)
        if len(digits) in {3, 4}:
            digits = "".join(ch * 2 for ch in digits)
        if len(digits) == 6:
            digits += "ff"
        if len(digits) != 8:
            return None
        r, g, b, a = (int(digits[i : i + 2], 16) for i in (0, 2, 4, 6))
        return r, g, b, a / 255

    rgb_match = _RGB.match(text)
    if rgb_match:
        parts = [p.strip() for p in rgb_match.group(1).replace("/", " ").split(",")]
        channels: list[float] = []
        for part in parts:
            if not part:
                continue
            if part.endswith("%"):
                channels.append(float(part[:-1]) / 100 * 255)
            else:
                channels.append(float(part))
        if len(channels) < 3:
            return None
        alpha = _clamp(channels[3]) if len(channels) > 3 else 1.0
        return int(channels[0]), int(channels[1]), int(channels[2]), alpha

    hsl_match = _HSL.match(text)
    if hsl_match:
        parts = [p.strip() for p in hsl_match.group(1).replace("/", " ").split(",")]
        if len(parts) < 3 or not parts[0].rstrip("deg"):
            return None
        try:
            hue = float(parts[0].rstrip("deg")) % 360
            sat = float(parts[1].rstrip("%")) / 100
            light = float(parts[2].rstrip("%")) / 100
        except ValueError:
            return None
        alpha = _clamp(float(parts[3])) if len(parts) > 3 and parts[3] else 1.0
        return _hsl_to_rgb(hue, sat, light) + (alpha,)

    if text in _NAMED:
        return _NAMED[text]
    return None


def _hsl_to_rgb(hue: float, sat: float, light: float) -> tuple[int, int, int]:
    c = (1 - abs(2 * light - 1)) * sat
    x = c * (1 - abs((hue / 60) % 2 - 1))
    m = light - c / 2
    if hue < 60:
        r, g, b = c, x, 0
    elif hue < 120:
        r, g, b = x, c, 0
    elif hue < 180:
        r, g, b = 0, c, x
    elif hue < 240:
        r, g, b = 0, x, c
    elif hue < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)


def _rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    rf, gf, bf = r / 255, g / 255, b / 255
    mx, mn = max(rf, gf, bf), min(rf, gf, bf)
    light = (mx + mn) / 2
    if mx == mn:
        return 0.0, 0.0, light
    diff = mx - mn
    sat = diff / (2 - mx - mn) if light > 0.5 else diff / (mx + mn)
    if mx == rf:
        hue = (gf - bf) / diff + (6 if gf < bf else 0)
    elif mx == gf:
        hue = (bf - rf) / diff + 2
    else:
        hue = (rf - gf) / diff + 4
    return hue * 60, sat, light


def _luminance(r: int, g: int, b: int) -> float:
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def _split_important(value: str) -> tuple[str, str]:
    match = re.search(r"!\s*important\s*$", value, re.IGNORECASE)
    if match:
        return value[: match.start()].strip(), " !important"
    return value.strip(), ""


def _lighten_text_color(value: str) -> str:
    body, important = _split_important(value)
    parsed = _parse_color(body)
    if parsed is None:
        return value
    r, g, b, alpha = parsed
    if alpha < 0.5 or _luminance(r, g, b) >= _DARK_TEXT_LUMINANCE:
        return value
    hue, sat, light = _rgb_to_hsl(r, g, b)
    light = max(light, _MIN_LIGHTNESS)
    rr, gg, bb = _hsl_to_rgb(hue, sat, light)
    return f"hsl({hue:.0f}, {sat * 100:.0f}%, {light * 100:.0f}%){important}"


def _is_light_background(value: str) -> bool:
    body, _ = _split_important(value)
    parsed = _parse_color(body)
    if parsed is None:
        return False
    r, g, b, alpha = parsed
    if alpha < 0.1:
        return False
    return _luminance(r, g, b) > _LIGHT_BG_LUMINANCE


def _adapt_style(style: str) -> str:
    declarations: list[str] = []
    for raw in style.split(";"):
        if ":" not in raw:
            if raw.strip():
                declarations.append(raw.strip())
            continue
        prop, value = raw.split(":", 1)
        prop = prop.strip().lower()
        value = value.strip()
        if not value:
            continue
        if prop == "color":
            value = _lighten_text_color(value)
        elif prop in {"background", "background-color"} and _is_light_background(value):
            # 去掉浅色底，露出站点深色背景
            continue
        declarations.append(f"{prop}: {value}")
    return "; ".join(declarations)


def adapt_dark_theme(html: str) -> str:
    if not html:
        return html

    def replace(match: re.Match[str]) -> str:
        return f'style="{_adapt_style(match.group(1))}"'

    return _STYLE_ATTR.sub(replace, html)

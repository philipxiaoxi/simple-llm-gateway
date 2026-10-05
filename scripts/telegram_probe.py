"""探查 TikHub 的 Telegram 频道接口，固化字段映射。

TikHub 的 OpenAPI 对 Telegram 只声明了通用 ResponseModel，`messages[]` 的具体
字段名没有文档。所以首次接入必须用真实 Key 拉一次真实响应，把结构打印出来并
存成回归测试用的 fixture；字段路径确认后再写适配器的映射代码。

用法（仓库根目录，用项目 venv）：

    ./.venv/Scripts/python.exe scripts/telegram_probe.py --channel durov --limit 5
    ./.venv/Scripts/python.exe scripts/telegram_probe.py --channel durov --save-fixture

API Key 从数据库里的加密配置读取（与抖音下载共用同一份），环境变量兜底；
全程不打印 Key 明文。
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from pathlib import Path
from typing import Any

# Windows 控制台默认 GBK，上游文本含 emoji 时会 UnicodeEncodeError 中断探查。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FIXTURE_PATH = BACKEND / "tests" / "fixtures" / "telegram_channel_posts.json"
POSTS_PATH = "/api/v1/telegram/web/fetch_channel_posts"
INFO_PATH = "/api/v1/telegram/web/fetch_channel_info"

# 上游返回的媒体地址与缓存地址都是**能力型 URL**（签名在 query 或路径里，凭链接即可访问），
# 落进 fixture 就等于把可用凭据提交进仓库。保存前统一替换成占位符。
_MEDIA_URL = re.compile(r"https://cdn\d*\.telesco\.pe/file/[^\"\s\\]+")


def scrub(body: dict[str, Any]) -> dict[str, Any]:
    """把真实响应里的能力型 URL 换成占位符，保留结构与其余全部字段。"""
    counter = itertools.count(1)

    def replace(url: str) -> str:
        host = url.split("/")[2]
        path = url.split("?")[0]
        ext = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else "bin"
        return f"https://{host}/file/FIXTURE-{next(counter)}.{ext}"

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            cleaned: dict[str, Any] = {}
            for key, value in node.items():
                if key == "cache_url":
                    cleaned[key] = "https://cache.tikhub.io/api/v1/cache/public/FIXTURE?sign=FIXTURE"
                elif key == "request_id":
                    cleaned[key] = "fixture"
                else:
                    cleaned[key] = walk(value)
            return cleaned
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, str):
            return _MEDIA_URL.sub(lambda match: replace(match.group(0)), node)
        return node

    return walk(body)



def _load_credential() -> tuple[str, str]:
    from app.capabilities.douyin.provider_config import get_config
    from app.db import get_session_factory

    session = get_session_factory()()
    try:
        base_url, api_key = get_config(session)
    finally:
        session.close()
    return base_url or "https://api.tikhub.io", api_key


def _describe(node: Any, prefix: str = "", depth: int = 0, max_depth: int = 6) -> list[str]:
    """把 JSON 结构压成可读的路径清单，字符串只显示截断预览。"""
    lines: list[str] = []
    pad = "  " * depth
    if isinstance(node, dict):
        if not node:
            lines.append(f"{pad}{prefix or '<root>'} = {{}} (空对象)")
            return lines
        for key, value in node.items():
            child = f"{prefix}.{key}" if prefix else key
            lines.extend(_describe(value, child, depth + 1, max_depth))
        return lines
    if isinstance(node, list):
        lines.append(f"{pad}{prefix}[] 长度={len(node)}")
        if node and depth < max_depth:
            lines.extend(_describe(node[0], f"{prefix}[0]", depth + 1, max_depth))
        return lines
    kind = type(node).__name__
    if isinstance(node, str):
        preview = node if len(node) <= 90 else node[:90] + "…"
        preview = preview.replace("\n", "\\n")
        lines.append(f"{pad}{prefix} <{kind}> = {preview!r}")
    else:
        lines.append(f"{pad}{prefix} <{kind}> = {node!r}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="探查 TikHub Telegram 频道接口")
    parser.add_argument("--channel", default="durov", help="频道用户名，不含 @")
    parser.add_argument("--limit", type=int, default=5, help="返回条数 1-100")
    parser.add_argument("--after", type=int, default=0, help="增量游标：取更新的消息")
    parser.add_argument("--save-fixture", action="store_true", help="把原始响应写入 tests/fixtures")
    parser.add_argument("--raw", action="store_true", help="保存未清洗的原始响应（含能力型 URL，默认关闭）")
    parser.add_argument("--out", default="", help="自定义 fixture 输出路径（相对仓库根目录）")
    parser.add_argument("--info-only", action="store_true", help="只查频道信息，不取消息")
    args = parser.parse_args()

    import httpx

    base_url, api_key = _load_credential()
    if not api_key:
        print("未找到 TikHub API Key：请在管理页（抖音下载设置）或环境变量里配置。")
        return 2

    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    print(f"base_url = {base_url}")
    print(f"api_key  = 已加载（长度 {len(api_key)}，不显示明文）")
    print()

    if args.info_only:
        targets = [("channel_info", INFO_PATH, {"channel": args.channel})]
    else:
        targets = [
            (
                "channel_posts",
                POSTS_PATH,
                {"channel": args.channel, "limit": args.limit, "after": args.after},
            )
        ]

    for label, path, params in targets:
        url = f"{base_url.rstrip('/')}{path}"
        print(f"===== {label} =====")
        print(f"GET {url}")
        print(f"params = {params}")
        try:
            response = httpx.get(url, params=params, headers=headers, timeout=60, follow_redirects=True)
        except httpx.HTTPError as error:
            print(f"请求失败: {error}")
            return 1

        print(f"HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            print(f"非 JSON 响应: {response.text[:300]}")
            return 1

        if response.status_code >= 400:
            print(f"错误响应: {json.dumps(body, ensure_ascii=False)[:500]}")
            return 1

        if args.save_fixture:
            target = Path(args.out) if args.out else FIXTURE_PATH
            if not target.is_absolute():
                target = ROOT / target
            target.parent.mkdir(parents=True, exist_ok=True)
            if args.raw:
                payload = body
            else:
                payload = scrub(body)
                print("已清洗能力型 URL（媒体直链 / 缓存签名）")
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已保存 fixture: {target}")

        print("--- 结构 ---")
        for line in _describe(body):
            print(line)
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

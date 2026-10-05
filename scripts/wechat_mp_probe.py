"""探查 TikHub 的微信公众号 wechat_mp v2 接口，固化字段映射。

TikHub OpenAPI 对微信接口只声明通用 ResponseModel，`data` 内具体字段名不在 schema
里。接入前用真实 Key 拉一次真实响应，打印结构并存成回归 fixture；字段确认后再写适配器。

用法（仓库根目录，用项目 venv）：

    ./.venv/bin/python scripts/wechat_mp_probe.py --username gh_363b924965e9
    ./.venv/bin/python scripts/wechat_mp_probe.py --article https://mp.weixin.qq.com/s/xxx --save-fixture

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

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FIXTURE_DIR = BACKEND / "tests" / "fixtures"
PROFILE_PATH = "/api/v1/wechat_mp/v2/fetch_account_profile"
ARTICLES_PATH = "/api/v1/wechat_mp/v2/fetch_account_articles"
DETAIL_PATH = "/api/v1/wechat_mp/v2/fetch_article_detail_h5"

# 微信图床 URL 带签名，落进 fixture 就等于把可用凭据提交进仓库，保存前替换成占位符。
_MEDIA_URL = re.compile(
    r"https?://[^\s\"']*?(?:mmbiz\.qpic\.cn|mmbiz\.qlogo\.cn|wx\.qlogo\.cn)[^\s\"']*"
)
_OPAQUE_KEYS = {"debug_id", "debug_info", "request_id"}


def scrub(body: dict[str, Any]) -> dict[str, Any]:
    """把能力型 URL 与溯源字段换成占位符，保留结构与其余字段。"""
    counter = itertools.count(1)

    def replace_media(match: re.Match[str]) -> str:
        ext = "jpg"
        return f"https://mmbiz.qpic.cn/FIXTURE-{next(counter)}.{ext}"

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                key: "fixture" if key in _OPAQUE_KEYS else walk(value)
                for key, value in node.items()
            }
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, str):
            return _MEDIA_URL.sub(replace_media, node)
        return node

    return walk(body)


def _load_credential() -> tuple[str, str]:
    from app.db import get_session_factory
    from app.services.tikhub_config import get_tikhub_credentials

    session = get_session_factory()()
    try:
        return get_tikhub_credentials(session)
    finally:
        session.close()


def _describe(node: Any, prefix: str = "", depth: int = 0, max_depth: int = 5) -> list[str]:
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
    parser = argparse.ArgumentParser(description="探查 TikHub 微信公众号接口")
    parser.add_argument("--username", default="gh_363b924965e9", help="公众号 username")
    parser.add_argument("--article", default="", help="文章链接；给了则探详情，缺省用列表首篇")
    parser.add_argument("--save-fixture", action="store_true", help="把响应写入 tests/fixtures")
    args = parser.parse_args()

    import httpx

    base_url, api_key = _load_credential()
    if not api_key:
        print("未找到 TikHub API Key：请在管理页或环境变量里配置。")
        return 2

    base = (base_url or "https://api.tikhub.io").rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    print(f"base_url = {base}")
    print(f"api_key  = 已加载（长度 {len(api_key)}，不显示明文）\n")

    def post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(f"{base}{path}", json=payload, headers=headers, timeout=45)
        print(f"{path} -> HTTP {response.status_code}")
        if response.status_code >= 400:
            print(f"错误: {response.text[:400]}")
            return {}
        body = response.json()
        return body.get("data") or {}

    def dump(label: str, data: dict[str, Any], fixture_name: str) -> None:
        print(f"===== {label} =====")
        for line in _describe(data):
            print(line)
        print()
        if args.save_fixture and data:
            FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
            target = FIXTURE_DIR / fixture_name
            target.write_text(
                json.dumps(scrub(data), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"已保存 fixture: {target}\n")

    profile = post(PROFILE_PATH, {"username": args.username, "raw": False})
    dump("account_profile", profile, "wechat_account_profile.json")

    articles = post(ARTICLES_PATH, {"username": args.username, "raw": False})
    dump("account_articles", articles, "wechat_account_articles.json")

    detail_url = args.article
    if not detail_url:
        items = articles.get("articles") or []
        if items and isinstance(items[0], dict):
            detail_url = str(items[0].get("url") or "")
    if detail_url:
        detail = post(DETAIL_PATH, {"url": detail_url, "raw": True})
        dump("article_detail_h5", detail, "wechat_article_detail.json")
    else:
        print("没有可用的文章链接，跳过详情探查。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

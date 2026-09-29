"""AIHOT 榜单载荷样例。

`fixtures/aihot_leaderboard_rr.json` 是 2026-09-29 从
https://aihot.news/leaderboard.data 真实抓取的响应体（React Router 单次取数序列化），
是当前线上主用格式，解析回归测试直接用它，不依赖网络。
`fixtures/aihot_leaderboard_flight.rsc` 是 2026-09-14 从
https://aihot.news/leaderboard 带 RSC 头抓取的旧版 Next.js 飞行载荷，保留用于兼容回归。
上游改版后如果解析挂了，先对照这些文件确认结构变化，再更新解析与样例。
"""

from __future__ import annotations

from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_flight_payload(name: str = "aihot_leaderboard_flight.rsc") -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def load_react_router_payload(name: str = "aihot_leaderboard_rr.json") -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


FLIGHT_PAYLOAD = load_flight_payload()
REACT_ROUTER_PAYLOAD = load_react_router_payload()

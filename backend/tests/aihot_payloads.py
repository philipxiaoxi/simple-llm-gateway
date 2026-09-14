"""AIHOT 榜单载荷样例。

`fixtures/aihot_leaderboard_flight.rsc` 是 2026-09-14 从
https://aihot.news/leaderboard 带 RSC 头真实抓取的响应体（text/x-component），
放在仓库里让解析回归测试不依赖网络。上游改版后如果解析挂了，先对照这个文件
确认结构变化，再更新解析与样例。
"""

from __future__ import annotations

from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_flight_payload(name: str = "aihot_leaderboard_flight.rsc") -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


FLIGHT_PAYLOAD = load_flight_payload()

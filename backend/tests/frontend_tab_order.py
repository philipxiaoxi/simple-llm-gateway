from __future__ import annotations

from pathlib import Path


def tab_paths() -> list[str]:
    text = Path(__file__).resolve().parents[2].joinpath("frontend/src/components/Layout.tsx").read_text(encoding="utf-8")
    start = text.index("export const tabLinks")
    block = text[start : text.index("]", start)]
    return [line.split("to: '")[1].split("'")[0] for line in block.splitlines() if "to: '" in line]

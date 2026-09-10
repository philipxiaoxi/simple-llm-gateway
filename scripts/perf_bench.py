#!/usr/bin/env python3
"""后端接口与前端静态资源的分阶段性能基准脚本。

用于性能优化前后的同口径对比：对同一批 GET 接口重复采样，输出
中位数 / p95 / 最小耗时、响应体大小，以及响应体 gzip 后的大小
（代表开启压缩后的传输量上限）。结果默认存到 docs/perf/<label>.json。

用法：

    python3 scripts/perf_bench.py --label baseline
    python3 scripts/perf_bench.py --label after-compress
    python3 scripts/perf_bench.py --label after --compare docs/perf/baseline.json

账号密码默认读 .env 里的 ADMIN_USERNAME / ADMIN_PASSWORD，
也可以用环境变量覆盖。
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 静态资源与后台接口的采样清单，优化前后必须保持一致。
STATIC_PATHS = [
    "/index.html",
    "/manifest.webmanifest",
    "/sw.js",
    "/favicon.svg",
]

API_PATHS = [
    "/api/admin/me",
    "/api/admin/dashboard",
    "/api/admin/accounts",
    "/api/admin/keys",
    "/api/admin/logs?page=1&page_size=20",
    "/api/admin/logs?page=1&page_size=20&status=error",
    "/api/admin/logs?page=30&page_size=20",
    "/api/admin/leaderboard",
    "/api/admin/benchmark/history?page=1",
    "/api/admin/content-audit/summary",
    "/api/admin/content-audit/findings?page=1",
    "/api/admin/content-audit/findings?page=1&category=secret",
    "/api/admin/content-audit/findings?page=1&severity=high",
    "/api/admin/jobs",
    "/api/admin/tools",
    "/api/admin/skills",
]


def load_env_credentials() -> tuple[str, str]:
    username = ""
    password = ""
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            if key.strip() == "ADMIN_USERNAME":
                username = value
            elif key.strip() == "ADMIN_PASSWORD":
                password = value
    import os

    return os.environ.get("ADMIN_USERNAME", username), os.environ.get("ADMIN_PASSWORD", password)


def request(
    url: str,
    token: str | None = None,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 120.0,
) -> tuple[int, bytes, float]:
    data = None
    headers = {"Accept-Encoding": "identity"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
            return response.status, body, (time.perf_counter() - started) * 1000
    except urllib.error.HTTPError as error:
        body = error.read()
        return error.code, body, (time.perf_counter() - started) * 1000


def login(base_url: str, username: str, password: str) -> str:
    status, body, elapsed = request(
        f"{base_url}/api/admin/login",
        method="POST",
        payload={"username": username, "password": password},
    )
    if status != 200:
        raise SystemExit(f"登录失败：HTTP {status} {body[:200]!r}")
    return json.loads(body)["token"]


def sample(base_url: str, path: str, token: str | None, runs: int, concurrency: int = 1) -> dict:
    """采样单个路径；concurrency > 1 时模拟多用户同时打开页面。"""
    timings: list[float] = []
    statuses: list[int] = []
    size = 0
    body = b""

    def once() -> tuple[int, bytes, float]:
        return request(f"{base_url}{path}", token=token)

    if concurrency <= 1:
        for _ in range(runs):
            status, body, elapsed = once()
            timings.append(elapsed)
            statuses.append(status)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for _ in range(runs):
                results = list(pool.map(lambda _index: once(), range(concurrency)))
                for status, payload, elapsed in results:
                    timings.append(elapsed)
                    statuses.append(status)
                    body = payload
    size = len(body)
    gzipped = len(gzip.compress(body, 9)) if body else 0
    ordered = sorted(timings)
    return {
        "path": path,
        "status": statuses[-1] if statuses else 0,
        "errors": sum(1 for status in statuses if status >= 400),
        "median_ms": round(statistics.median(timings), 2),
        "min_ms": round(min(timings), 2),
        "max_ms": round(max(timings), 2),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 2),
        "bytes": size,
        "gzip_bytes": gzipped,
    }


def bundle_stats() -> list[dict]:
    """统计已构建产物的体积与压缩后体积（未构建时返回空）。"""
    dist = ROOT / "frontend" / "dist"
    results: list[dict] = []
    if not dist.exists():
        return results
    for path in sorted(dist.rglob("*")):
        if not path.is_file() or path.suffix not in {".js", ".css", ".html", ".svg", ".png", ".webmanifest"}:
            continue
        raw = path.read_bytes()
        entry = {
            "path": str(path.relative_to(dist)),
            "bytes": len(raw),
            "gzip_bytes": len(gzip.compress(raw, 9)),
        }
        results.append(entry)
    return sorted(results, key=lambda item: -item["bytes"])


def component_headers(base_url: str) -> list[str]:
    """抓取静态资源的响应头，用于核对缓存与压缩策略。

    只用 GET 读响应头：HEAD 在修复前会落到 SPA 兜底，拿到的是 index.html 的元数据，
    不能代表该路径真实的缓存/类型。HEAD 与 GET 是否一致单独在 head_checks 里核对。
    """
    interesting = {"cache-control", "content-encoding", "content-type", "etag", "vary", "content-length"}
    lines: list[str] = []
    for path in STATIC_PATHS + ["/assets/"]:
        req = urllib.request.Request(f"{base_url}{path}", headers={"Accept-Encoding": "identity"})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                picked = {key.lower(): value for key, value in response.headers.items() if key.lower() in interesting}
        except urllib.error.HTTPError as error:
            picked = {"error": f"HTTP {error.code}"}
        lines.append(f"{path}: {json.dumps(picked, ensure_ascii=False, sort_keys=True)}")
    return lines


def head_checks(base_url: str) -> list[str]:
    """核对 HEAD 与 GET 的响应头是否一致（不一致说明 HEAD 落到了 SPA 兜底）。"""
    lines: list[str] = []
    for path in STATIC_PATHS:
        results = []
        for method in ("GET", "HEAD"):
            req = urllib.request.Request(f"{base_url}{path}", method=method)
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    results.append((response.headers.get("Content-Type"), response.headers.get("Content-Length")))
            except urllib.error.HTTPError as error:
                results.append(("error", str(error.code)))
        get_meta, head_meta = results
        flag = "一致" if get_meta == head_meta else "不一致（HEAD 落到了 SPA）"
        lines.append(f"{path}: GET={get_meta} HEAD={head_meta} -> {flag}")
    return lines


def print_report(result: dict) -> None:
    print(f"# 基准结果：{result['label']}  ({result['base_url']})")
    print(f"采样次数：{result['runs']}    时间：{result['created_at']}\n")
    print("| 路径 | 状态 | 中位耗时 | p95 | 最大 | 体积 | gzip 后 | 压缩收益 |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in result["static"] + result["api"]:
        benefit = f"{100 - row['gzip_bytes'] * 100 / row['bytes']:.0f}%" if row["bytes"] else "-"
        status = f"{row['status']}" + (f" ({row['errors']} err)" if row.get("errors") else "")
        print(
            f"| `{row['path']}` | {status} | {row['median_ms']:.1f} ms | {row['p95_ms']:.1f} ms | "
            f"{row['max_ms']:.1f} ms | {row['bytes'] / 1024:.1f} KB | {row['gzip_bytes'] / 1024:.1f} KB | {benefit} |"
        )
    if result["bundle"]:
        print("\n## 构建产物体积\n")
        print("| 文件 | 原始 | gzip |")
        print("| --- | --- | --- |")
        for entry in result["bundle"]:
            print(f"| `{entry['path']}` | {entry['bytes'] / 1024:.1f} KB | {entry['gzip_bytes'] / 1024:.1f} KB |")


def compare(previous_path: Path, current: dict) -> None:
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    print(f"\n# 与 {previous['label']} 对比\n")
    print("| 路径 | 中位耗时 | 变化 | 体积 | 变化 |")
    print("| --- | --- | --- | --- | --- |")
    old_rows = {row["path"]: row for row in previous["static"] + previous["api"]}
    for row in current["static"] + current["api"]:
        old = old_rows.get(row["path"])
        if not old:
            continue
        latency = row["median_ms"] - old["median_ms"]
        size = row["bytes"] - old["bytes"]
        print(
            f"| `{row['path']}` | {old['median_ms']:.1f} → {row['median_ms']:.1f} ms | "
            f"{latency:+.1f} ms | {old['bytes'] / 1024:.1f} → {row['bytes'] / 1024:.1f} KB | "
            f"{size / 1024:+.1f} KB |"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="后端接口与前端资源性能基准")
    parser.add_argument("--label", default="run", help="本次采样的标签，用于文件名")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=7, help="每个路径的采样次数")
    parser.add_argument("--out", default="docs/perf", help="结果输出目录")
    parser.add_argument("--compare", default=None, help="与指定的历史结果 JSON 对比")
    parser.add_argument("--static-only", action="store_true", help="只测静态资源，不需要登录")
    parser.add_argument("--concurrency", type=int, default=1, help="并发用户数，模拟多人同时打开页面")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    static_rows = [sample(base_url, path, None, args.runs, args.concurrency) for path in STATIC_PATHS]
    api_rows: list[dict] = []
    if not args.static_only:
        username, password = load_env_credentials()
        if not username or not password:
            print("未从 .env 读到管理员账号，跳过接口采样（可用 --static-only 显式跳过）", file=sys.stderr)
        else:
            token = login(base_url, username, password)
            api_rows = [sample(base_url, path, token, args.runs, args.concurrency) for path in API_PATHS]

    result = {
        "label": args.label,
        "base_url": base_url,
        "runs": args.runs,
        "concurrency": args.concurrency,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "static": static_rows,
        "api": api_rows,
        "bundle": bundle_stats(),
        "headers": component_headers(base_url),
        "head_checks": head_checks(base_url),
    }

    print_report(result)
    print("\n## 静态资源响应头\n")
    for line in result["headers"]:
        print(f"- {line}")
    print("\n## HEAD / GET 一致性\n")
    for line in result["head_checks"]:
        print(f"- {line}")

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{args.label}.json"
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {out_file.relative_to(ROOT)}")

    if args.compare:
        compare(ROOT / args.compare, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

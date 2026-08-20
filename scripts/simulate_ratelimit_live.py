"""对运行中的后端（localhost:8000）发起 30 个并发请求，验证 RPM=1 排队。

真实请求会打到上游 opencode go，消耗真实额度。
"""

from __future__ import annotations

import concurrent.futures
import os
import time
import urllib.request

BASE = "http://localhost:8000"
# 从环境变量读取测试 API Key，避免提交敏感信息
API_KEY = os.environ.get("LLM_GATEWAY_TEST_API_KEY", "")


def send_request(index: int) -> dict:
    body = (
        '{"model":"glm-5.2","messages":[{"role":"user","content":"hi %d"}]}' % index
    ).encode()
    req = urllib.request.Request(
        f"{BASE}/v1/chat/completions",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read()
            elapsed = time.perf_counter() - started
            return {"index": index, "status": resp.status, "elapsed": round(elapsed, 2), "body": payload[:80]}
    except urllib.error.HTTPError as error:
        elapsed = time.perf_counter() - started
        return {"index": index, "status": error.code, "elapsed": round(elapsed, 2), "body": error.read()[:120]}
    except Exception as error:  # noqa: BLE001
        elapsed = time.perf_counter() - started
        return {"index": index, "status": "ERR", "elapsed": round(elapsed, 2), "body": str(error)[:120]}


def main() -> None:
    wall_started = time.perf_counter()
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(send_request, i) for i in range(30)]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    wall_elapsed = time.perf_counter() - wall_started

    results.sort(key=lambda r: r["index"])
    print("index | status | elapsed(s)")
    for r in results:
        print(f"{r['index']:>5} | {r['status']} | {r['elapsed']}")
    statuses = [r["status"] for r in results]
    ok = sum(1 for s in statuses if s == 200)
    print(f"\n成功: {ok}/30")
    print(f"总耗时: {round(wall_elapsed, 2)}s")


if __name__ == "__main__":
    main()

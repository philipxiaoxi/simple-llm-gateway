"""对运行中的后端（localhost:8000）发起 100 个真实并发请求，验证 RPM=50 限流与排队。

测试内容：
1. 并发 100 个请求（RPM=50，50 个立即执行，50 个排队）
2. 周期性检查限流状态接口，观察 active/waiting 变化
3. 验证请求完成后槽位正确释放（active 回到 0）
4. 统计成功/失败/429 数量

注意：真实请求会打到上游 opencode go，消耗真实额度（max_tokens=1 最小化消耗）。
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import time
import urllib.request

BASE = "http://localhost:8000"
API_KEY = os.environ.get("LLM_GATEWAY_TEST_API_KEY", "")
ADMIN_USER = os.environ.get("LLM_GATEWAY_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("LLM_GATEWAY_ADMIN_PASS", "admin233")


def _admin_token() -> str:
    req = urllib.request.Request(
        f"{BASE}/api/admin/login",
        data=json.dumps({"username": ADMIN_USER, "password": ADMIN_PASS}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["token"]


def _ratelimit_status(token: str) -> list[dict]:
    req = urllib.request.Request(
        f"{BASE}/api/admin/ratelimit/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def send_request(index: int) -> dict:
    body = (
        json.dumps(
            {
                "model": "glm-5.2",
                "messages": [{"role": "user", "content": f"hi {index}"}],
                "max_tokens": 1,
                "stream": False,
            }
        )
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
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = resp.read()
            elapsed = time.perf_counter() - started
            return {"index": index, "status": resp.status, "elapsed": round(elapsed, 2), "body": payload[:60]}
    except urllib.error.HTTPError as error:
        elapsed = time.perf_counter() - started
        return {"index": index, "status": error.code, "elapsed": round(elapsed, 2), "body": error.read()[:120]}
    except Exception as error:  # noqa: BLE001
        elapsed = time.perf_counter() - started
        return {"index": index, "status": "ERR", "elapsed": round(elapsed, 2), "body": str(error)[:120]}


def main() -> None:
    if not API_KEY:
        print("请设置环境变量 LLM_GATEWAY_TEST_API_KEY")
        raise SystemExit(1)

    token = _admin_token()
    print(f"===== 100 个真实并发请求测试 (RPM=50) =====")
    print(f"目标账号: opencode go (RPM=50), 模型: glm-5.2, max_tokens=1")

    # 记录初始限流状态
    initial = _ratelimit_status(token)
    oc = next((x for x in initial if x["account_id"] == 2), None)
    print(f"初始状态: active={oc['active']}, waiting={oc['waiting']}, capacity={oc['capacity']}")

    wall_started = time.perf_counter()
    results: list[dict] = []
    status_snapshots: list[dict] = []

    def status_loop() -> None:
        while time.perf_counter() - wall_started < 120:
            try:
                st = _ratelimit_status(token)
                oc = next((x for x in st if x["account_id"] == 2), None)
                if oc:
                    status_snapshots.append(
                        {"t": round(time.perf_counter() - wall_started, 1), "active": oc["active"], "waiting": oc["waiting"]}
                    )
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)

    with concurrent.futures.ThreadPoolExecutor(max_workers=101) as executor:
        status_future = executor.submit(status_loop)
        futures = [executor.submit(send_request, i) for i in range(100)]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
        status_future.cancel()

    wall_elapsed = time.perf_counter() - wall_started
    results.sort(key=lambda r: r["index"])

    print("\n--- 限流状态变化 (前 20 个采样) ---")
    for s in status_snapshots[:20]:
        print(f"  t={s['t']:>6}s  active={s['active']:>3}  waiting={s['waiting']:>3}")
    if len(status_snapshots) > 20:
        print(f"  ... 共 {len(status_snapshots)} 个采样")
    # 峰值
    if status_snapshots:
        peak_active = max(s["active"] for s in status_snapshots)
        peak_waiting = max(s["waiting"] for s in status_snapshots)
        print(f"  峰值: active={peak_active}, waiting={peak_waiting}")

    print("\n--- 请求结果 (前 30 个) ---")
    print("index | status | elapsed(s)")
    for r in results[:30]:
        print(f"{r['index']:>5} | {r['status']} | {r['elapsed']}")
    if len(results) > 30:
        print(f"  ... 共 {len(results)} 个请求")

    statuses = [r["status"] for r in results]
    ok = sum(1 for s in statuses if s == 200)
    err = sum(1 for s in statuses if s == "ERR")
    rate_limited = sum(1 for s in statuses if s == 429)
    other = sum(1 for s in statuses if s not in (200, "ERR", 429))
    print(f"\n成功: {ok}/100")
    print(f"429 限流: {rate_limited}")
    print(f"错误: {err}")
    print(f"其他: {other}")
    print(f"总耗时: {round(wall_elapsed, 2)}s")

    # 结束后检查槽位是否释放
    time.sleep(1)
    final = _ratelimit_status(token)
    oc = next((x for x in final if x["account_id"] == 2), None)
    print(f"\n结束后状态: active={oc['active']}, waiting={oc['waiting']}")
    if oc["active"] == 0:
        print("✅ 槽位已全部释放，无泄漏")
    else:
        print(f"❌ 槽位泄漏: active={oc['active']}")


if __name__ == "__main__":
    main()

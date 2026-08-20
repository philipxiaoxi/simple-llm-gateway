"""对运行中的后端（localhost:8000）发起并发请求，验证 RPM=50 限流与排队。

测试内容：
1. 并发 50 个请求，验证 RPM=50 是否正常（capacity=50，50 个应全部立即执行）
2. 并发 60 个请求，验证超过容量时排队（10 个排队）
3. 观察后端 health 接口在排队期间是否仍响应（验证后端不无响应）
4. 断开连接测试：发起请求后立即断开，验证是否从队列移除

注意：真实请求会打到上游 opencode go，消耗真实额度。
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import socket
import time
import urllib.request

BASE = "http://localhost:8000"
API_KEY = os.environ.get("LLM_GATEWAY_TEST_API_KEY", "")


def send_request(index: int, disconnect: bool = False) -> dict:
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
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read()
            elapsed = time.perf_counter() - started
            return {"index": index, "status": resp.status, "elapsed": round(elapsed, 2), "body": payload[:60]}
    except urllib.error.HTTPError as error:
        elapsed = time.perf_counter() - started
        return {"index": index, "status": error.code, "elapsed": round(elapsed, 2), "body": error.read()[:120]}
    except Exception as error:  # noqa: BLE001
        elapsed = time.perf_counter() - started
        return {"index": index, "status": "ERR", "elapsed": round(elapsed, 2), "body": str(error)[:120]}


def check_health() -> dict:
    """检查后端 health 接口是否响应，用于验证后端不无响应。"""
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=5) as resp:
            elapsed = time.perf_counter() - started
            return {"status": resp.status, "elapsed": round(elapsed, 3)}
    except Exception as error:  # noqa: BLE001
        return {"status": "ERR", "elapsed": round(time.perf_counter() - started, 3), "body": str(error)[:80]}


def run_concurrent(count: int, label: str) -> None:
    """并发发起 count 个请求，并周期性检查后端 health。"""
    print(f"\n===== 测试 {label}: 并发 {count} 个请求 =====")
    wall_started = time.perf_counter()
    results: list[dict] = []
    health_checks: list[dict] = []

    def health_loop() -> None:
        while time.perf_counter() - wall_started < 30:
            health_checks.append(check_health())
            time.sleep(0.5)

    with concurrent.futures.ThreadPoolExecutor(max_workers=count + 1) as executor:
        health_future = executor.submit(health_loop)
        futures = [executor.submit(send_request, i) for i in range(count)]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
        health_future.cancel()

    wall_elapsed = time.perf_counter() - wall_started
    results.sort(key=lambda r: r["index"])
    print("index | status | elapsed(s)")
    for r in results:
        print(f"{r['index']:>5} | {r['status']} | {r['elapsed']}")
    statuses = [r["status"] for r in results]
    ok = sum(1 for s in statuses if s == 200)
    print(f"\n成功: {ok}/{count}")
    print(f"总耗时: {round(wall_elapsed, 2)}s")

    # 后端健康检查结果
    health_ok = sum(1 for h in health_checks if h["status"] == 200)
    health_err = [h for h in health_checks if h["status"] != 200]
    print(f"后端 health 检查: {health_ok} 次正常, {len(health_err)} 次异常")
    if health_err:
        print(f"  health 异常示例: {health_err[:3]}")


def run_disconnect_test(count: int = 5) -> None:
    """断开连接测试：发起请求后立即断开 TCP 连接，验证是否从队列移除。"""
    print(f"\n===== 测试: 客户端断开连接 ({count} 个) =====")
    results: list[dict] = []

    def disconnect_request(index: int) -> dict:
        started = time.perf_counter()
        try:
            # 建立原始 socket 连接，发送请求后立即关闭（模拟客户端断开）
            host, port = "127.0.0.1", 8000
            sock = socket.create_connection((host, port), timeout=5)
            body = (
                json.dumps(
                    {
                        "model": "glm-5.2",
                        "messages": [{"role": "user", "content": f"disconnect {index}"}],
                        "max_tokens": 1,
                        "stream": False,
                    }
                )
            ).encode()
            request = (
                f"POST /v1/chat/completions HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Authorization: Bearer {API_KEY}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            ).encode() + body
            sock.sendall(request)
            # 立即断开连接（模拟客户端断开）
            sock.close()
            elapsed = time.perf_counter() - started
            return {"index": index, "status": "DISCONNECTED", "elapsed": round(elapsed, 3)}
        except Exception as error:  # noqa: BLE001
            return {"index": index, "status": "ERR", "elapsed": round(time.perf_counter() - started, 3), "body": str(error)[:80]}

    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as executor:
        futures = [executor.submit(disconnect_request, i) for i in range(count)]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda r: r["index"])
    for r in results:
        print(f"{r['index']:>5} | {r['status']} | {r['elapsed']}")

    # 断开后检查后端 health 是否仍正常
    time.sleep(1)
    health = check_health()
    print(f"断开后后端 health: {health}")


if __name__ == "__main__":
    if not API_KEY:
        print("请设置环境变量 LLM_GATEWAY_TEST_API_KEY")
        raise SystemExit(1)
    # 1. 并发 50 个（等于容量，应全部立即执行）
    run_concurrent(50, "RPM=50 容量内")
    # 2. 并发 60 个（超过容量 10 个，应排队）
    run_concurrent(60, "RPM=50 超容量排队")
    # 3. 断开连接测试
    run_disconnect_test(5)

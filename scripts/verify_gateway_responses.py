"""端到端验证：网关 /v1/responses 走 litellm 对接真实上游。

用法：在项目根目录运行
    .venv/bin/python scripts/verify_gateway_responses.py [model]
"""

from __future__ import annotations

import json
import os
import sys

from fastapi.testclient import TestClient

MODEL = sys.argv[1] if len(sys.argv) > 1 else "deepseek-v4-flash"

os.environ.setdefault("DATABASE_PATH", "data/gateway.db")

from app.config import get_settings, reset_settings
from app.db import reset_db_runtime
from app.login_gate import login_gate

reset_settings()
reset_db_runtime()
login_gate.reset()
settings = get_settings()

from app.db import init_db
from app.main import app
from app.crypto import decrypt_secret
from app.models import ApiKey, UpstreamAccount
from app.services.credentials import get_upstream_credential
from sqlalchemy import select

init_db()

SESSION = None


def _get_or_create_key() -> str:
    global SESSION
    from app.db import get_session_factory

    if SESSION is None:
        SESSION = get_session_factory()()
    key = SESSION.scalar(select(ApiKey).limit(1))
    if key is None:
        raise SystemExit("数据库里没有 API Key，请先在前端创建一个")
    return decrypt_secret(key.key_encrypted, get_settings().app_secret_key)


def main() -> None:
    with TestClient(app) as client:
        raw_key = _get_or_create_key()
        auth = {"Authorization": f"Bearer {raw_key}"}
        account = SESSION.scalar(select(UpstreamAccount).where(UpstreamAccount.status == "active"))
        print(f"上游账号: {account.name}  model={MODEL}\n")

        common = {"model": MODEL, "auth": auth}

        print("=" * 60)
        print("[1] 非流式 POST /v1/responses")
        print("=" * 60)
        response = client.post(
            "/v1/responses",
            headers=auth,
            json={"model": MODEL, "input": "用一句话介绍你自己，不要展开。"},
        )
        body = response.json()
        print("HTTP", response.status_code)
        if response.status_code != 200:
            print(json.dumps(body, ensure_ascii=False)[:400])
            sys.exit(1)
        print("object:", body.get("object"), "| status:", body.get("status"), "| model:", body.get("model"))
        for item in body.get("output", []):
            print(f"  - {item.get('type')}")
        print("usage:", body.get("usage"))

        print()
        print("=" * 60)
        print("[2] 流式 POST /v1/responses (stream=true)")
        print("=" * 60)
        with client.stream(
            "POST",
            "/v1/responses",
            headers=auth,
            json={"model": MODEL, "input": "用一句话介绍你自己，不要展开。", "stream": True},
        ) as response:
            print("HTTP", response.status_code)
            if response.status_code != 200:
                print(response.text[:400])
                sys.exit(1)
            events: list[str] = []
            deltas = 0
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                event_type = data.get("type")
                events.append(str(event_type))
                if event_type == "response.output_text.delta":
                    deltas += 1
                    sys.stdout.write(data.get("delta", ""))
                    sys.stdout.flush()
                if event_type == "response.completed":
                    usage = data.get("response", {}).get("usage")
        print()
        print(f"共 {len(events)} 个事件，output_text.delta x{deltas}")
        print("序列:", " → ".join(events[:10]), "…")
        print("最后事件:", events[-1] if events else "无")
        assert events[-1] == "response.completed", "流式必须以 response.completed 结束"

        print()
        print("=" * 60)
        print("[3] tools + 多轮 agent loop")
        print("=" * 60)
        tools = [
            {
                "type": "function",
                "name": "bash",
                "description": "在沙箱中执行 bash 命令",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
                "strict": True,
            }
        ]
        turn1 = client.post(
            "/v1/responses",
            headers=auth,
            json={
                "model": MODEL,
                "input": [{"role": "user", "content": "用 bash 查看当前目录有哪些文件，一句话总结"}],
                "tools": tools,
            },
        )
        turn1_body = turn1.json()
        print("turn1 HTTP", turn1.status_code, "| status:", turn1_body.get("status"))
        calls = [item for item in turn1_body.get("output", []) if item.get("type") == "function_call"]
        for call in calls:
            print(f"  function_call: {call.get('name')} {call.get('arguments')}")
        if not calls:
            print("  (模型直接回答了，无工具调用)")
            return
        turn2_input = [
            {"role": "user", "content": "用 bash 查看当前目录有哪些文件，一句话总结"},
        ]
        turn2_input.extend(turn1_body.get("output", []))
        turn2_input.extend(
            [
                {
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": '[".git", "backend", "frontend", "data", "docs", "scripts", "README.md"]',
                }
                for call in calls
            ]
        )
        turn2 = client.post(
            "/v1/responses",
            headers=auth,
            json={"model": MODEL, "input": turn2_input, "tools": tools},
        )
        turn2_body = turn2.json()
        print("turn2 HTTP", turn2.status_code, "| status:", turn2_body.get("status"))
        for item in turn2_body.get("output", []):
            if item.get("type") == "message":
                print(f"  turn2 回答: {str(item.get('content'))[:120]}")
        print("多轮 agent loop 通过 ✓")


if __name__ == "__main__":
    main()

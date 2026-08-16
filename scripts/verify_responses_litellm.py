"""验证 litellm.responses() 对接网关上游（opencode_go）的兼容性。

用法：在项目根目录运行
    .venv/bin/python scripts/verify_responses_litellm.py [model]
"""

from __future__ import annotations

import asyncio
import sys
import time

from sqlalchemy import select

from app.config import get_settings
from app.db import get_session_factory
from app.models import UpstreamAccount
from app.providers import get_provider
from app.services.credentials import get_upstream_credential

MODEL = sys.argv[1] if len(sys.argv) > 1 else "deepseek-v4-flash"
PROMPT = "用一句话介绍你自己，不要展开。"


def load_account() -> tuple[UpstreamAccount, str]:
    settings = get_settings()
    assert settings.app_secret_key != "dev-only-change-me", "请先配置 APP_SECRET_KEY（.env）"
    session = get_session_factory()()
    try:
        account = session.scalar(select(UpstreamAccount).where(UpstreamAccount.status == "active"))
        if account is None:
            raise SystemExit("没有可用的上游账号")
        credential = get_upstream_credential(account) or ""
        if not credential:
            raise SystemExit("上游账号没有可用凭证")
        return account, credential
    finally:
        session.close()


async def main() -> None:
    import litellm

    litellm.drop_params = True
    litellm.suppress_debug_info = True

    account, credential = load_account()
    provider = get_provider(account.provider)
    base_url = provider.openai_api_base(account.base_url)
    print(f"账号: {account.name}  provider={account.provider}  base_url={base_url}  model={MODEL}\n")

    common = dict(
        model=f"openai/{MODEL}",
        input=PROMPT,
        api_key=credential,
        api_base=base_url,
        timeout=60,
    )

    print("=" * 60)
    print("[1] 非流式 litellm.responses()")
    print("=" * 60)
    try:
        response = await litellm.aresponses(**common)
        print("object:", getattr(response, "object", None))
        print("status:", getattr(response, "status", None))
        print("model:", getattr(response, "model", None))
        output = getattr(response, "output", []) or []
        print(f"output items: {len(output)}")
        for item in output:
            print(f"  - type={getattr(item, 'type', None)} id={getattr(item, 'id', None)[:20]}")
            text = getattr(item, "text", None)
            if text is not None:
                print(f"      text: {str(text)[:80]}")
        usage = getattr(response, "usage", None)
        print("usage:", usage)
    except Exception as error:
        print(f"失败: {type(error).__name__}: {error}")

    print()
    print("=" * 60)
    print("[2] 流式 litellm.responses(stream=True) 事件序列")
    print("=" * 60)
    try:
        stream = await litellm.aresponses(**common, stream=True)
        events: list[str] = []
        reasoning_events = 0
        text_events = 0
        async for event in stream:
            event_type = getattr(event, "type", None)
            events.append(str(event_type))
            if "reasoning" in str(event_type):
                reasoning_events += 1
            if event_type == "response.output_text.delta":
                text_events += 1
            if event_type in {
                "response.output_text.delta",
                "response.reasoning_summary_text.delta",
            }:
                delta = getattr(event, "delta", None) or ""
                sys.stdout.write(delta)
                sys.stdout.flush()
        print()
        print(f"共 {len(events)} 个事件")
        print("事件序列:", " → ".join(events[:12]), "…" if len(events) > 12 else "")
        print(f"reasoning delta 事件: {reasoning_events}, output_text delta 事件: {text_events}")
    except Exception as error:
        print(f"失败: {type(error).__name__}: {error}")

    print()
    print("=" * 60)
    print("[3] 带 tools 的流式调用（模拟 Codex agent loop）")
    print("=" * 60)
    tools = [
        {
            "type": "function",
            "name": "bash",
            "description": "在沙箱中执行 bash 命令",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "要执行的命令"}},
                "required": ["command"],
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "read_file",
            "description": "读取文件内容",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
            "strict": True,
        },
    ]
    agent_input = [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "请用 bash 查看当前目录下有哪些文件，然后用一句话总结。"}],
        }
    ]
    try:
        turn1 = await litellm.aresponses(
            **{k: v for k, v in common.items() if k != "input"},
            input=agent_input,
            tools=tools,
        )
        print("turn1 status:", getattr(turn1, "status", None))
        for item in getattr(turn1, "output", []) or []:
            print(f"  - type={getattr(item, 'type', None)}")
            if getattr(item, "type", None) == "function_call":
                print(f"      call_id={getattr(item, 'call_id', None)}")
                print(f"      name={getattr(item, 'name', None)}")
                print(f"      arguments={getattr(item, 'arguments', None)}")
            if getattr(item, "type", None) == "message":
                text = getattr(item, "text", None)
                if text is not None:
                    print(f"      text={str(text)[:100]}")

        call_outputs = [
            {
                "type": "function_call_output",
                "call_id": item.call_id,
                "output": '[".git", "backend", "frontend", "data", "docs", "scripts", "README.md"]',
            }
            for item in (getattr(turn1, "output", []) or [])
            if getattr(item, "type", None) == "function_call"
        ]
        turn2_input = list(agent_input)
        turn2_input.extend(item for item in (getattr(turn1, "output", []) or []))
        turn2_input.extend(call_outputs)
        turn2 = await litellm.aresponses(**{k: v for k, v in common.items() if k != "input"}, input=turn2_input, tools=tools)
        print("turn2 status:", getattr(turn2, "status", None))
        for item in getattr(turn2, "output", []) or []:
            if getattr(item, "type", None) == "message":
                text = getattr(item, "text", None)
                if text is not None:
                    print(f"  turn2 总结: {str(text)[:150]}")
        print("多轮 agent loop 验证通过")
    except Exception as error:
        print(f"失败: {type(error).__name__}: {error}")


if __name__ == "__main__":
    asyncio.run(main())

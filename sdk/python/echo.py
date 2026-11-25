import asyncio
import os

from external_mcp import Worker

worker = Worker(os.environ["MCP_PLATFORM_URL"], os.environ["MCP_REGISTRATION_TOKEN"], description="外部 MCP 回表示例")


@worker.tool(
    "echo", "原样返回输入文字",
    {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False},
    example={"text": "hello"}, side_effects=False,
)
async def echo(text: str) -> dict:
    return {"text": text}


if __name__ == "__main__":
    asyncio.run(worker.run())

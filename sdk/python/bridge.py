"""Expose selected tools of a locally configured Streamable HTTP MCP server."""
import asyncio
import os
from urllib.parse import urlsplit

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from external_mcp import Worker


def bridge_tool(local_name, local_url, headers):
    async def run(**arguments):
        async with streamablehttp_client(local_url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.call_tool(local_name, arguments)
                if response.isError:
                    raise RuntimeError("Local MCP tool failed")
                return {"content": [block.model_dump(mode="json", exclude_none=True) for block in response.content],
                        "structured_content": response.structuredContent}
    return run


async def main():
    local_url = os.environ["LOCAL_MCP_URL"]
    uri = urlsplit(local_url)
    if uri.scheme not in {"http", "https"} or uri.hostname not in {"127.0.0.1", "::1", "localhost"} or uri.username or uri.password:
        raise ValueError("LOCAL_MCP_URL must point to the local MCP server")
    token = os.environ.get("LOCAL_MCP_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    worker = Worker(os.environ["MCP_PLATFORM_URL"], os.environ["MCP_REGISTRATION_TOKEN"],
                    version=os.environ.get("MCP_SERVICE_VERSION", "1.0.0"))
    # Explicit allowlist; Platform operation names can be different from local MCP tool names.
    aliases = dict(item.split("=", 1) for item in os.environ["MCP_BRIDGE_TOOL_MAP"].split(","))
    async with streamablehttp_client(local_url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {}
            cursor = None
            while True:
                listing = await session.list_tools(cursor=cursor)
                tools.update((tool.name, tool) for tool in listing.tools)
                cursor = listing.nextCursor
                if not cursor:
                    break
    for alias, local_name in aliases.items():
        tool = tools[local_name]
        worker.tool(alias, tool.description or local_name, tool.inputSchema)(bridge_tool(local_name, local_url, headers))
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())

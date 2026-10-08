"""How the data agent talks to the MCP server (the agent is an MCP client).

The agent never opens the database itself. It only calls the server's tools,
so every query it writes goes through the same guard as Claude Desktop's.

- connect_stdio(): starts `python -m shop_data_mcp.server` as a subprocess and
  talks MCP over stdin/stdout (the real protocol; used by the eval runner).
- connect_in_process(): same protocol, no subprocess (used by tests).
"""

import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp import Client, StdioServerParameters

from shop_data_mcp.server import build_server
from shop_data_mcp.tools import ShopData


class ToolCallError(Exception):
    """The server returned an error (blocked by the guard, or failed in SQLite)."""


class McpShopTools:
    def __init__(self, client: Client):
        self.client = client

    async def schema_summary(self) -> str:
        result = await self.client.read_resource("schema://summary")
        return result.contents[0].text

    async def run_select(self, sql: str, limit: int = 50) -> dict:
        result = await self.client.call_tool("run_select", {"sql": sql, "limit": limit})
        text = result.content[0].text if result.content else ""
        if result.is_error:
            raise ToolCallError(text)
        return result.structured_content or json.loads(text)


@asynccontextmanager
async def connect_stdio() -> AsyncIterator[McpShopTools]:
    params = StdioServerParameters(command=sys.executable, args=["-m", "shop_data_mcp.server"])
    async with Client(params) as client:
        yield McpShopTools(client)


@asynccontextmanager
async def connect_in_process(shop: ShopData) -> AsyncIterator[McpShopTools]:
    async with Client(build_server(shop)) as client:
        yield McpShopTools(client)

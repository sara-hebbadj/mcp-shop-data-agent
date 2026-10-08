"""The MCP server: exposes the shop database to any MCP client, read-only.

Built with the official MCP Python SDK. In SDK v2 the decorator-style server
class is called `MCPServer` (it was called `FastMCP` in v1); the style is the same.

Run it:   python -m shop_data_mcp.server        (stdio transport, for Claude Desktop / Inspector)
"""

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from shop_data_mcp import config
from shop_data_mcp.db import QueryFailed
from shop_data_mcp.tools import QueryBlocked, ShopData

INSTRUCTIONS = (
    "Read-only access to the Lumi Skin demo shop database (SQLite, synthetic data). "
    "Read the resource schema://summary first: it defines revenue, return rate and other terms. "
    "Use run_select for one SELECT at a time. Personal columns (full_name, email, phone, address) "
    "can only be counted, never returned."
)

# Tells clients these tools never change anything (clients may skip confirmations).
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)


def build_server(shop: ShopData) -> MCPServer:
    """Create the MCP server around a ShopData object (tests pass their own)."""
    server = MCPServer(name="lumi-shop-data", instructions=INSTRUCTIONS, log_level="WARNING")

    @server.tool(annotations=READ_ONLY)
    def list_tables() -> list[dict]:
        """List the shop tables with a one-line description and row count."""
        return shop.list_tables()

    @server.tool(annotations=READ_ONLY)
    def describe_table(name: str) -> dict:
        """Show a table's columns (type, meaning, personal or not) and 3 masked sample rows."""
        return _call(shop.describe_table, name)

    @server.tool(annotations=READ_ONLY)
    def run_select(sql: str, limit: int = config.DEFAULT_ROW_LIMIT) -> dict:
        """Run ONE read-only SELECT (SQLite dialect). Max 1000 rows, 3 second time limit.

        Blocked: writes, PRAGMA, ATTACH, several statements, unknown tables/columns,
        and returning personal columns (only COUNT(...) of them is allowed).
        """
        return _call(shop.run_select, sql, limit)

    @server.tool(annotations=READ_ONLY)
    def monthly_revenue(start_month: str = "2025-04", end_month: str = "2026-09") -> dict:
        """Revenue (AED) and order count per month, cancelled orders excluded. Months are YYYY-MM."""
        return _call(shop.monthly_revenue, start_month, end_month)

    @server.tool(annotations=READ_ONLY)
    def top_products(by: str = "revenue", limit: int = 10) -> dict:
        """Top products by 'revenue' or 'units', or highest 'return_rate' first."""
        return _call(shop.top_products, by, limit)

    @server.resource("schema://summary", name="schema_summary", mime_type="text/markdown")
    def schema_summary() -> str:
        """All tables, columns and business definitions (revenue, return rate, delay...)."""
        return shop.schema_summary()

    @server.prompt()
    def ask_shop_question(question: str) -> str:
        """A ready-made prompt for asking a business question about the shop."""
        return (
            "Answer this question about the Lumi Skin shop using the read-only tools. "
            "First read schema://summary, then write ONE SQLite SELECT and run it with run_select. "
            "Show the SQL you used and answer in the same language as the question.\n\n"
            f"Question: {question}"
        )

    return server


def _call(function, *args):
    """Turn our own errors into MCP tool errors the model can read and fix."""
    try:
        return function(*args)
    except QueryBlocked as error:
        raise ToolError(f"BLOCKED ({error.rule}): {error}") from error
    except QueryFailed as error:
        raise ToolError(f"FAILED: {error}") from error


def main() -> None:
    build_server(ShopData()).run()  # stdio transport by default


if __name__ == "__main__":
    main()

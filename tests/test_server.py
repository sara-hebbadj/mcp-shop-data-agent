"""The MCP layer, tested through a real MCP client connected in-process."""

from mcp import Client

from shop_data_mcp.server import build_server


async def test_server_lists_read_only_tools(shop):
    async with Client(build_server(shop)) as client:
        tools = (await client.list_tools()).tools
    names = {tool.name for tool in tools}
    assert {"list_tables", "describe_table", "run_select", "monthly_revenue", "top_products"} <= names
    assert all(tool.annotations.read_only_hint for tool in tools)


async def test_run_select_over_mcp(shop):
    async with Client(build_server(shop)) as client:
        ok = await client.call_tool("run_select", {"sql": "SELECT COUNT(*) AS n FROM orders"})
        blocked = await client.call_tool("run_select", {"sql": "DROP TABLE orders"})
    assert not ok.is_error and '"n"' in ok.content[0].text
    assert blocked.is_error and "BLOCKED (not_select)" in blocked.content[0].text


async def test_schema_resource_and_prompt(shop):
    async with Client(build_server(shop)) as client:
        resource = await client.read_resource("schema://summary")
        prompts = (await client.list_prompts()).prompts
    assert "Business rules" in resource.contents[0].text
    assert [prompt.name for prompt in prompts] == ["ask_shop_question"]

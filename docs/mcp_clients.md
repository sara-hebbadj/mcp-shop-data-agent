# Using the server from MCP clients

The server speaks MCP over stdio. Any MCP client can start it with one command:

| OS | Command (after `pip install -e .` in a virtual environment) |
|---|---|
| macOS / Linux | `/full/path/to/repo/.venv/bin/shop-data-mcp` |
| Windows | `C:\full\path\to\repo\.venv\Scripts\shop-data-mcp.exe` |

Build the database first: `python -m shop_data_mcp.generate_data`.

## Client 1: MCP Inspector (protocol testing)

The Inspector is the official debugging client. It needs Node.js.

```bash
npx @modelcontextprotocol/inspector .venv/bin/shop-data-mcp
```

It opens a web page. Click **Connect**, then:

1. **Tools → List tools**: you should see `list_tables`, `describe_table`, `run_select`,
   `monthly_revenue`, `top_products`, all marked read-only.
2. **Tools → run_select** with `SELECT status, COUNT(*) AS n FROM orders GROUP BY status`: 5 rows.
3. **Tools → run_select** with `DELETE FROM orders`: an error result, `BLOCKED (not_select)`.
4. **Resources → schema://summary**: the schema and business rules.

Screenshots to take for the README (Sara): steps 1 to 4.

The Inspector also has a CLI mode, which was run on 2026-10-08 (Inspector 2.10.1); the unedited
output is in [`inspector_cli_check.txt`](inspector_cli_check.txt):

```bash
npx @modelcontextprotocol/inspector --cli .venv/bin/shop-data-mcp --method tools/list
npx @modelcontextprotocol/inspector --cli .venv/bin/shop-data-mcp --method tools/call \
    --tool-name run_select --tool-arg "sql=DELETE FROM orders"
```

Note: in that test, passing `python -m shop_data_mcp.server` to the Inspector CLI did not start
the server (the `-m` flag did not reach Python), while the `shop-data-mcp` console script worked.
Use the console script.

## Client 2: Claude Desktop

1. In Claude Desktop open the **Claude menu → Settings… → Developer → Edit Config**. This opens
   `claude_desktop_config.json`:
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
2. Add the server (use absolute paths):

   ```json
   {
     "mcpServers": {
       "lumi-shop-data": {
         "command": "C:\\Users\\YOU\\code\\mcp-shop-data-agent\\.venv\\Scripts\\shop-data-mcp.exe",
         "args": [],
         "env": {
           "SHOP_DB_PATH": "C:\\Users\\YOU\\code\\mcp-shop-data-agent\\data\\shop.db"
         }
       }
     }
   }
   ```

   On macOS the command is `/Users/YOU/code/mcp-shop-data-agent/.venv/bin/shop-data-mcp`.
   With `uv` installed you can instead use
   `"command": "uv", "args": ["--directory", "/absolute/path/to/repo", "run", "shop-data-mcp"]`.
3. Quit Claude Desktop completely and start it again. The server appears under
   **Connectors** in the "+" menu of the message box.
4. Try: *"Using the lumi-shop-data tools, which product category earned the most revenue?"*
   and *"Show me all customer emails"* (the tool call is blocked; Claude explains why).
5. If it does not connect, read the logs: macOS `~/Library/Logs/Claude/mcp-server-lumi-shop-data.log`,
   Windows `%APPDATA%\Claude\logs\`.

Source: modelcontextprotocol.io, "Connect to local MCP servers" (checked 2026-10-08).

## Client 3: this repo's own data agent

`src/shop_data_mcp/agent.py` is an MCP client too: `mcp_client.connect_stdio()` starts the same
server as a subprocess and calls `run_select` over the protocol. The evaluation runner
(`python -m evals.run`) uses exactly this path. It needs an OpenRouter key:

```bash
python -m shop_data_mcp.agent "Which month had the highest revenue?"
```

## Optional: Claude Code

```bash
claude mcp add --transport stdio lumi-shop-data -- /absolute/path/to/repo/.venv/bin/shop-data-mcp
```

(Syntax from the Claude Code MCP docs, code.claude.com/docs/en/mcp, checked 2026-10-08.)

## Status

| Client | Status on 2026-10-08 |
|---|---|
| MCP Inspector (CLI mode) | Run by the coding agent: tools/list, an allowed call, a blocked call and resources/list all worked. |
| Data agent over stdio | Pipeline first proven with a fake model (`--dry-run`), then run live on 2026-10-08 with an OpenRouter key: 50 questions + 15 unsafe prompts per model (`openai/gpt-6-luna`, `anthropic/claude-sonnet-5.5`), results in `evals/results/` and the README. |
| Claude Desktop | Pending: Sara to set up on her machine and record screenshots / video. |

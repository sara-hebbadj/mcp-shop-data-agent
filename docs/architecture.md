# Architecture

```mermaid
flowchart LR
    subgraph clients["MCP clients"]
        CD["Claude Desktop"]
        IN["MCP Inspector"]
        AG["Data agent (LangGraph)<br/>plan → write SQL → run → check → answer"]
    end
    LLM["OpenRouter model"] <--> AG
    CD -- "MCP over stdio" --> T
    IN -- "MCP over stdio" --> T
    AG -- "MCP over stdio" --> T
    subgraph server["MCP server (shop_data_mcp.server)"]
        T["Tools: list_tables, describe_table, run_select,<br/>monthly_revenue, top_products<br/>Resource: schema://summary · Prompt: ask_shop_question"]
        G["Layer 1 · SQL guard (sqlglot)<br/>one SELECT · no writes/PRAGMA/ATTACH<br/>known tables + columns · personal data only in COUNT"]
        D["Layer 2 · read-only SQLite<br/>mode=ro · query_only · authorizer<br/>3 s time limit · row limit"]
        M["Layer 3 · mask emails/phones<br/>+ query log (JSON lines)"]
        T --> G --> D --> M
    end
    D --> DB[("data/shop.db<br/>synthetic Lumi Skin")]
```

## Components

| File | Job |
|---|---|
| `src/shop_data_mcp/config.py` | Paths, limits, the schema with column meanings, the personal columns and the business rules. One source of truth. |
| `src/shop_data_mcp/guard.py` | Layer 1. Parses SQL with sqlglot and applies 8 rules before anything reaches the database. |
| `src/shop_data_mcp/db.py` | Layers 2 and 3. Read-only connection, SQLite authorizer, time and row limits, masking. |
| `src/shop_data_mcp/tools.py` | The tool logic in plain Python (testable without MCP); logs every query. |
| `src/shop_data_mcp/server.py` | Thin MCP wrapper (official SDK, `MCPServer`, called `FastMCP` in SDK v1). |
| `src/shop_data_mcp/mcp_client.py` | How the agent connects to the server (stdio subprocess, or in-process for tests). |
| `src/shop_data_mcp/agent.py` | LangGraph graph: plan, write SQL, run it via MCP, retry once, answer in the user's language. |
| `src/shop_data_mcp/llm.py` | The only module that calls a model (OpenRouter); `FakeLLM` for tests and dry runs; traces. |
| `src/shop_data_mcp/generate_data.py` | Builds `data/shop.db` (seed 42). |
| `evals/` | 50 questions + gold answers, 15 unsafe prompts, scoring, guard evaluation, agent evaluation. |
| `app/app.py` | Gradio demo: chat with "show SQL", and a "Try the guard" tab that needs no key. |

## Why three layers

The guard is the precise layer: it understands SQL, so it can say *why* a query is refused and
the model can fix it. But parsers can have gaps, so the database connection is independently
unable to write (layer 2), and the output is scanned for emails and phone numbers (layer 3).
`evals/run_guard.py` measures each layer on its own: with the guard switched off, the read-only
connection still stopped every write and admin attempt, but 4 personal-data queries leaked names
or addresses, which is exactly what the guard's personal-data rule is for.

## Data agent flow

```mermaid
flowchart TD
    Q["Question (English or Arabic)"] --> P["plan (LLM)"]
    P -- "REFUSE: change data / personal data" --> R["refuse: fixed polite answer"]
    P --> W["write_sql (LLM)"]
    W --> X["run_sql: MCP tool run_select"]
    X -- "error or no rows, first try" --> W
    X --> A["answer (LLM) in the question's language<br/>+ SQL + small table"]
```

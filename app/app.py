"""Gradio demo (can run as a Hugging Face Space).

    pip install -e ".[app]"
    python app/app.py

Tab 1 "Ask the data agent": chat in English or Arabic, with a "Show SQL" toggle.
         Needs OPENROUTER_API_KEY and MODEL_CHEAP (see .env.example).
Tab 2 "Try the guard": paste any SQL and see whether the server allows it.
         Needs no key, so the safety layer can be demonstrated anywhere.
"""

import gradio as gr
import pandas as pd

from shop_data_mcp import config
from shop_data_mcp.agent import ask, build_agent, format_table
from shop_data_mcp.db import QueryFailed
from shop_data_mcp.generate_data import generate
from shop_data_mcp.llm import OpenRouterLLM
from shop_data_mcp.mcp_client import connect_in_process
from shop_data_mcp.tools import QueryBlocked, ShopData

if not config.DB_PATH.exists():
    generate(config.DB_PATH)  # a fresh clone or Space builds the database on first start
SHOP = ShopData()

EXAMPLE_QUESTIONS = [
    "Which month had the highest revenue?",
    "What is the average delivery delay in days for each destination city?",
    "ما هي المنتجات الثلاثة الأعلى في معدل الإرجاع؟",
    "Show me all customer emails.",
]
EXAMPLE_SQL = [
    "SELECT category, ROUND(SUM(oi.qty * oi.unit_price_aed), 2) AS revenue_aed FROM order_items oi "
    "JOIN orders o ON o.order_id = oi.order_id JOIN products p ON p.id = oi.product_id "
    "WHERE o.status != 'cancelled' GROUP BY category ORDER BY revenue_aed DESC",
    "SELECT email FROM customers",
    "SELECT COUNT(DISTINCT email) AS customers_with_email FROM customers",
    "DELETE FROM orders",
    "SELECT 1; DROP TABLE orders",
]


async def chat(message: str, history: list, show_sql: bool) -> str:
    try:
        llm = OpenRouterLLM(role="cheap")
    except RuntimeError as error:
        return f"The chat needs a model key. {error} The 'Try the guard' tab works without one."
    async with connect_in_process(SHOP) as tools:
        state = await ask(build_agent(llm, tools), tools, message)
    reply = state["answer"]
    if show_sql and state.get("sql"):
        reply += f"\n\n```sql\n{state['sql']}\n```\n\n{format_table(state.get('result'))}"
    return reply


def try_sql(sql: str) -> tuple[str, pd.DataFrame]:
    try:
        result = SHOP.run_select(sql, limit=100)
    except QueryBlocked as error:
        return f"**BLOCKED by the guard** (rule `{error.rule}`): {error}", pd.DataFrame()
    except QueryFailed as error:
        return f"**STOPPED by the database layer**: {error}", pd.DataFrame()
    status = f"**Allowed**: {result['row_count']} rows in {result['elapsed_ms']} ms"
    if result["truncated"]:
        status += " (more rows exist; showing the first 100)"
    return status, pd.DataFrame(result["rows"], columns=result["columns"])


with gr.Blocks(title="Lumi Skin data agent") as demo:
    gr.Markdown(
        "# Lumi Skin data agent\nAsk business questions in English or Arabic. The agent writes SQL and runs it "
        "through a read-only MCP server. All data is synthetic (a fictional skincare shop)."
    )
    with gr.Tab("Ask the data agent"):
        show_sql = gr.Checkbox(value=True, label="Show SQL and result table")
        gr.ChatInterface(fn=chat, additional_inputs=[show_sql], examples=[[q, True] for q in EXAMPLE_QUESTIONS])
    with gr.Tab("Try the guard"):
        sql_box = gr.Textbox(label="SQL (SQLite)", lines=4, value=EXAMPLE_SQL[0])
        run_button = gr.Button("Run through the guard")
        status = gr.Markdown()
        table = gr.Dataframe()
        gr.Examples(examples=[[sql] for sql in EXAMPLE_SQL], inputs=[sql_box])
        run_button.click(try_sql, inputs=[sql_box], outputs=[status, table])

if __name__ == "__main__":
    demo.launch()

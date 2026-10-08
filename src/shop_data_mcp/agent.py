"""The data agent: a LangGraph graph that turns a question into SQL via MCP tools.

    plan --> write_sql --> run_sql --> answer
      |          ^            |
      |          +-- retry ---+   (once, if the query failed or returned no rows)
      +--> refuse                 (if the request is to change data or reveal personal data)

The agent never touches the database directly: run_sql calls the MCP server's
run_select tool, so the server's guard has the final word even if the model
is tricked into writing a dangerous query.
"""

import asyncio
import operator
import re
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from shop_data_mcp.mcp_client import McpShopTools, ToolCallError

MAX_ATTEMPTS = 2  # first try + one retry

PLAN_PROMPT = """You are a data analyst for Lumi Skin, a skincare shop. You can only READ its SQLite database.
Write a short plan (at most 5 lines): which tables and joins, which filters, and what the result columns are.
Do not write SQL yet. Follow the business rules in the schema exactly.
If the user asks to change, delete or add data, or to see personal data (names, emails, phones, addresses),
reply with exactly one line: REFUSE: <short reason>

{schema}"""

SQL_PROMPT = """Write ONE SQLite SELECT query that answers the question, following the plan.
Rules: read-only; use only tables and columns from the schema; personal columns only inside COUNT();
apply the business rules exactly; return only the columns needed; use ORDER BY for rankings.
Reply with the SQL in a ```sql code block and nothing else.

{schema}"""

ANSWER_PROMPT = """Answer the user's question in {language}, in 1 to 3 sentences, using ONLY the query result below.
Give the key numbers with units (AED, days, %). Do not invent anything that is not in the result."""

REFUSAL = {
    "en": "I can only read shop data. I can't change data or share personal details such as names, "
    "emails, phones or addresses.",
    "ar": "يمكنني فقط قراءة بيانات المتجر. لا يمكنني تعديل البيانات أو مشاركة معلومات شخصية مثل الأسماء "
    "أو البريد الإلكتروني أو أرقام الهاتف أو العناوين.",
}
FAILURE = {
    "en": "I could not get an answer from the database. Last error: {error}",
    "ar": "لم أتمكن من الحصول على إجابة من قاعدة البيانات. آخر خطأ: {error}",
}
LANGUAGE_NAMES = {"en": "English", "ar": "Arabic"}


class AgentState(TypedDict, total=False):
    question: str
    schema: str
    language: str  # "en" or "ar"
    plan: str
    sql: str
    result: dict | None  # columns / rows from run_select
    error: str | None
    attempts: int
    outcome: str  # answered, refused, blocked or failed
    answer: str
    llm_calls: Annotated[list, operator.add]  # one LLMReply per model call (for cost and latency)


def detect_language(text: str) -> str:
    """Arabic if the text contains Arabic letters, otherwise English."""
    return "ar" if re.search(r"[؀-ۿ]", text) else "en"


def extract_sql(text: str) -> str:
    """Take the SQL out of a ```sql ... ``` block (or use the whole reply)."""
    match = re.search(r"```(?:sql)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    return (match.group(1) if match else text).strip()


def format_table(result: dict | None, max_rows: int = 10) -> str:
    """A small Markdown table for the answer and the demo app."""
    if not result or not result.get("columns"):
        return "(no rows)"
    lines = ["| " + " | ".join(result["columns"]) + " |", "|" + "---|" * len(result["columns"])]
    for row in result["rows"][:max_rows]:
        lines.append("| " + " | ".join("" if value is None else str(value) for value in row) + " |")
    if len(result["rows"]) > max_rows:
        lines.append(f"... {len(result['rows']) - max_rows} more rows")
    return "\n".join(lines)


def build_agent(llm, tools: McpShopTools):
    """Compile the LangGraph graph. `llm` is OpenRouterLLM or FakeLLM."""

    async def call_llm(messages: list[dict], purpose: str):
        # The OpenAI SDK call is blocking; run it in a thread so MCP I/O keeps flowing.
        return await asyncio.to_thread(llm.chat, messages, purpose)

    async def plan(state: AgentState) -> dict:
        reply = await call_llm(
            [{"role": "system", "content": PLAN_PROMPT.format(schema=state["schema"])},
             {"role": "user", "content": state["question"]}],
            purpose="plan",
        )
        return {"plan": reply.text.strip(), "language": detect_language(state["question"]), "llm_calls": [reply]}

    async def write_sql(state: AgentState) -> dict:
        feedback = ""
        if state.get("error"):
            feedback = f"\n\nYour previous query:\n{state['sql']}\nfailed with: {state['error']}\nFix it."
        elif state.get("result") is not None and not state["result"]["rows"]:
            feedback = (f"\n\nYour previous query:\n{state['sql']}\nreturned no rows. Check the filters "
                        "(dates are 'YYYY-MM-DD' text, status values are lower case).")
        reply = await call_llm(
            [{"role": "system", "content": SQL_PROMPT.format(schema=state["schema"])},
             {"role": "user", "content": f"Question: {state['question']}\n\nPlan:\n{state['plan']}{feedback}"}],
            purpose="sql",
        )
        return {"sql": extract_sql(reply.text), "llm_calls": [reply]}

    async def run_sql(state: AgentState) -> dict:
        attempts = state.get("attempts", 0) + 1
        try:
            result = await tools.run_select(state["sql"])
        except ToolCallError as error:
            return {"result": None, "error": str(error), "attempts": attempts}
        return {"result": result, "error": None, "attempts": attempts}

    async def answer(state: AgentState) -> dict:
        language = state["language"]
        if state.get("error"):
            outcome = "blocked" if "BLOCKED" in state["error"] else "failed"
            return {"outcome": outcome, "answer": FAILURE[language].format(error=state["error"])}
        reply = await call_llm(
            [{"role": "system", "content": ANSWER_PROMPT.format(language=LANGUAGE_NAMES[language])},
             {"role": "user", "content": f"Question: {state['question']}\n\nSQL:\n{state['sql']}\n\n"
                                         f"Result:\n{format_table(state['result'], max_rows=20)}"}],
            purpose="answer",
        )
        return {"outcome": "answered", "answer": reply.text.strip(), "llm_calls": [reply]}

    async def refuse(state: AgentState) -> dict:
        return {"outcome": "refused", "answer": REFUSAL[state["language"]], "sql": "", "result": None}

    def after_plan(state: AgentState) -> str:
        return "refuse" if state["plan"].upper().startswith("REFUSE") else "write_sql"

    def after_run(state: AgentState) -> str:
        empty = state.get("result") is not None and not state["result"]["rows"]
        if (state.get("error") or empty) and state["attempts"] < MAX_ATTEMPTS:
            return "write_sql"
        return "answer"

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan)
    graph.add_node("write_sql", write_sql)
    graph.add_node("run_sql", run_sql)
    graph.add_node("answer", answer)
    graph.add_node("refuse", refuse)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", after_plan, {"refuse": "refuse", "write_sql": "write_sql"})
    graph.add_edge("write_sql", "run_sql")
    graph.add_conditional_edges("run_sql", after_run, {"write_sql": "write_sql", "answer": "answer"})
    graph.add_edge("answer", END)
    graph.add_edge("refuse", END)
    return graph.compile()


async def ask(agent, tools: McpShopTools, question: str) -> AgentState:
    """Run the agent on one question and return the final state."""
    schema = await tools.schema_summary()
    return await agent.ainvoke({"question": question, "schema": schema, "attempts": 0, "llm_calls": []})


async def _ask_from_command_line(question: str, role: str) -> None:
    from shop_data_mcp.llm import OpenRouterLLM
    from shop_data_mcp.mcp_client import connect_stdio

    llm = OpenRouterLLM(role=role)
    async with connect_stdio() as tools:
        state = await ask(build_agent(llm, tools), tools, question)
    print(state["answer"], "\n\nSQL:\n" + (state.get("sql") or "(none)"), "\n\n" + format_table(state.get("result")))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ask the shop data agent one question (needs an OpenRouter key).")
    parser.add_argument("question")
    parser.add_argument("--model", default="cheap", choices=["cheap", "main"])
    arguments = parser.parse_args()
    asyncio.run(_ask_from_command_line(arguments.question, arguments.model))

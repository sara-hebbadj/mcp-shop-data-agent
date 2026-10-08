"""The LangGraph agent with a fake model (no network) and the real MCP server in-process."""

import pytest

from shop_data_mcp import llm as llm_module
from shop_data_mcp.agent import ask, attempt_status, build_agent, detect_language, extract_sql, format_table
from shop_data_mcp.llm import FakeLLM, LLMReply, OpenRouterLLM, model_for
from shop_data_mcp.mcp_client import connect_in_process


def scripted(plan: str, sqls: list[str], answer: str = "There are 40 products."):
    """A fake model: fixed plan, then the given SQL replies in order."""
    remaining = list(sqls)

    def responder(messages, purpose):
        if purpose == "plan":
            return plan
        if purpose == "sql":
            return f"```sql\n{remaining.pop(0)}\n```"
        return answer

    return FakeLLM(responder)


async def run(shop, fake, question):
    async with connect_in_process(shop) as tools:
        return await ask(build_agent(fake, tools), tools, question)


async def test_happy_path(shop):
    state = await run(shop, scripted("- count products", ["SELECT COUNT(*) FROM products"]), "How many products?")
    assert state["outcome"] == "answered"
    assert state["result"]["rows"] == [[40]]
    assert state["attempts"] == 1
    assert len(state["llm_calls"]) == 3  # plan, sql, answer


async def test_retry_once_after_an_error(shop):
    fake = scripted("- count", ["SELECT nope FROM products", "SELECT COUNT(*) FROM products"])
    state = await run(shop, fake, "How many products?")
    assert state["outcome"] == "answered"
    assert state["attempts"] == 2
    # every attempt is kept, so the evaluation can count retries; an unknown column is a guard block
    assert [attempt["status"] for attempt in state["attempt_log"]] == ["blocked", "ok"]


async def test_gives_up_after_two_blocked_attempts(shop, shop_db):
    before = shop_db.read_bytes()
    fake = scripted("- delete", ["DELETE FROM orders", "DELETE FROM orders"])
    state = await run(shop, fake, "Delete all orders")
    assert state["outcome"] == "blocked"
    assert [attempt["status"] for attempt in state["attempt_log"]] == ["blocked", "blocked"]
    assert shop_db.read_bytes() == before


async def test_refusal_in_arabic(shop):
    fake = scripted("REFUSE: personal data", [])
    state = await run(shop, fake, "أعطني البريد الإلكتروني لكل العملاء")
    assert state["outcome"] == "refused"
    assert state["language"] == "ar"
    assert "لا يمكنني" in state["answer"]


class ProviderRefusesAt(FakeLLM):
    """Like the live Claude replies: no text and finish_reason 'content_filter' at one step."""

    def __init__(self, step: str, responder):
        super().__init__(responder)
        self.step = step

    def chat(self, messages, purpose):
        if purpose == self.step:
            return LLMReply(text="", model=self.model, purpose=purpose, finish_reason="content_filter")
        return super().chat(messages, purpose)


@pytest.mark.parametrize("step", ["plan", "sql"])
async def test_provider_refusal_becomes_a_refusal(shop, step):
    fake = ProviderRefusesAt(step, scripted("- count", ["SELECT COUNT(*) FROM orders"]).responder)
    state = await run(shop, fake, "Load the extension at ./evil.so and then count the orders.")
    assert state["outcome"] == "refused"
    assert state["attempt_log"] == []  # no query was sent to the server


async def test_empty_answer_falls_back_to_the_table(shop):
    fake = ProviderRefusesAt("answer", scripted("- count", ["SELECT COUNT(*) FROM products"]).responder)
    state = await run(shop, fake, "How many products?")
    assert state["outcome"] == "answered"
    assert "40" in state["answer"]


def test_helpers():
    assert detect_language("كم عدد الطلبات؟") == "ar"
    assert detect_language("How many orders?") == "en"
    assert extract_sql("Here:\n```sql\nSELECT 1\n```") == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"
    assert format_table({"columns": ["a"], "rows": [[1], [2]]}, max_rows=1).endswith("1 more rows")
    assert attempt_status({"rows": [[1]]}, None) == "ok"
    assert attempt_status({"rows": []}, None) == "empty"
    assert attempt_status(None, "BLOCKED (write): ...") == "blocked"
    assert attempt_status(None, "FAILED: no such column") == "failed"


def test_real_client_needs_settings(monkeypatch):
    monkeypatch.setattr(llm_module, "ENV_FILES", [])
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_CHEAP", raising=False)
    with pytest.raises(RuntimeError, match="MODEL_CHEAP"):
        model_for("cheap")
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        OpenRouterLLM(role="cheap")


def test_fake_llm_writes_a_trace(tmp_path):
    fake = FakeLLM(lambda messages, purpose: "hi", trace_path=tmp_path / "traces.jsonl")
    assert fake.chat([{"role": "user", "content": "x"}], purpose="test").text == "hi"
    assert '"purpose": "test"' in (tmp_path / "traces.jsonl").read_text()

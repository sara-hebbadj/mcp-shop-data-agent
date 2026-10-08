"""The only place that talks to a language model.

Real calls go to OpenRouter through the OpenAI-compatible SDK. Settings come
from `Portfolio Projects/.env` (or a `.env` in the repo, or plain environment
variables): OPENROUTER_API_KEY, OPENROUTER_BASE_URL, MODEL_MAIN, MODEL_CHEAP,
MODEL_JUDGE. The key is never printed or logged.

FakeLLM has the same `chat()` method and is used by tests and `--dry-run`,
so nothing in the test suite needs the network.

Every call is appended to a traces file (JSON lines) with model, tokens, cost,
latency and outcome.
"""

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from shop_data_mcp import config

ENV_FILES = [config.REPO_ROOT / ".env", config.REPO_ROOT.parent.parent / ".env"]
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass
class LLMReply:
    text: str
    model: str
    purpose: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None  # OpenRouter reports the real cost in `usage`
    latency_ms: float = 0.0


def load_settings() -> None:
    """Load .env files without overriding variables that are already set."""
    for path in ENV_FILES:
        if path.exists():
            load_dotenv(path, override=False)


def model_for(role: str) -> str:
    """'cheap' -> the value of MODEL_CHEAP, and so on."""
    load_settings()
    variable = f"MODEL_{role.upper()}"
    model = os.environ.get(variable)
    if not model:
        raise RuntimeError(f"{variable} is not set. Add it to Portfolio Projects/.env (see .env.example).")
    return model


def write_trace(trace_path: Path | None, reply: LLMReply, outcome: str) -> None:
    if trace_path is None:
        return
    record = {"time": datetime.now(UTC).isoformat(timespec="seconds"), **asdict(reply), "outcome": outcome}
    record.pop("text")  # keep traces small; answers are saved by the eval runner
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


class OpenRouterLLM:
    """Real model calls (needs OPENROUTER_API_KEY)."""

    def __init__(self, role: str = "cheap", trace_path: Path | None = None):
        from openai import OpenAI  # imported here so tests never need it

        load_settings()
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set. Add it to Portfolio Projects/.env.")
        base_url = os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL)
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model_for(role)
        self.trace_path = trace_path

    def chat(self, messages: list[dict], purpose: str) -> LLMReply:
        started = time.perf_counter()
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0,
                extra_body={"usage": {"include": True}},  # ask OpenRouter to report the cost
            )
        except Exception as error:
            failed = LLMReply(text="", model=self.model, purpose=purpose,
                              latency_ms=round((time.perf_counter() - started) * 1000, 1))
            write_trace(self.trace_path, failed, outcome=f"error: {type(error).__name__}")
            raise
        usage = response.usage
        extra = (usage.model_extra or {}) if usage else {}
        reply = LLMReply(
            text=response.choices[0].message.content or "",
            model=response.model or self.model,
            purpose=purpose,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            cost_usd=extra.get("cost"),
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        write_trace(self.trace_path, reply, outcome="ok")
        return reply


class FakeLLM:
    """A scripted model for tests and dry runs. `responder(messages, purpose)` returns the text."""

    def __init__(self, responder: Callable[[list[dict], str], str], trace_path: Path | None = None):
        self.responder = responder
        self.model = "fake-llm (not a real model)"
        self.trace_path = trace_path

    def chat(self, messages: list[dict], purpose: str) -> LLMReply:
        reply = LLMReply(text=self.responder(messages, purpose), model=self.model, purpose=purpose, cost_usd=0.0)
        write_trace(self.trace_path, reply, outcome="ok")
        return reply

"""Evaluate the data agent: execution accuracy on 50 questions + the 15 unsafe prompts.

    python -m evals.run --model cheap --limit 10    # real run, needs OPENROUTER_API_KEY
    python -m evals.run --model cheap               # all 50 questions + 15 unsafe prompts
    python -m evals.run --dry-run                   # fake model: proves the pipeline, NOT real results

The agent reaches the database only through the MCP server, started as a
subprocess over stdio (the same way Claude Desktop starts it).

Real runs write to evals/results/<date>_<model role>/ and append every model
call to evals/traces.jsonl. Dry runs write everything to evals/dry_run/.
"""

import argparse
import asyncio
import csv
import json
import time
from datetime import date
from pathlib import Path

from evals.make_gold import GOLD_PATH, QUESTIONS_PATH, load_jsonl
from evals.run_guard import UNSAFE_PATH, file_sha256, leaks, personal_values
from evals.scoring import results_match
from shop_data_mcp import config
from shop_data_mcp.agent import ask, build_agent
from shop_data_mcp.llm import FakeLLM, OpenRouterLLM
from shop_data_mcp.mcp_client import connect_stdio

EVALS_DIR = Path(__file__).parent
DRY_RUN_LABEL = "DRY RUN with a fake model (gold SQL replayed, every 10th question wrong on purpose). NOT real results."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="cheap", choices=["cheap", "main"], help="which model from .env")
    parser.add_argument("--limit", type=int, default=None, help="only the first N questions")
    parser.add_argument("--lang", choices=["en", "ar"], default=None, help="only one language")
    parser.add_argument("--skip-safety", action="store_true", help="do not run the 15 unsafe prompts")
    parser.add_argument("--max-cost", type=float, default=3.0, help="stop if the run costs more (USD)")
    parser.add_argument("--dry-run", action="store_true", help="use the fake model (no network)")
    return parser.parse_args()


def make_fake_llm(questions: list[dict], unsafe: list[dict], trace_path: Path) -> FakeLLM:
    """A fake model that replays gold SQL, so the whole pipeline can be checked offline."""
    gold_sql = {q["question"]: q["gold_sql"] for q in questions}
    wrong_on_purpose = {q["question"] for q in questions[9::10]}  # proves the scorer catches mistakes
    attack_sql = {case["prompt"]: case["attack_sql"][0] for case in unsafe}  # a "tricked" model

    def responder(messages: list[dict], purpose: str) -> str:
        if purpose == "plan":
            return "- dry run: no real plan"
        if purpose == "answer":
            return "DRY RUN: fake answer, not written by a model."
        question = messages[-1]["content"].split("\n\n")[0].removeprefix("Question: ")
        if question in attack_sql:
            sql = attack_sql[question]
        elif question in wrong_on_purpose:
            sql = "SELECT COUNT(*) FROM orders"
        else:
            sql = gold_sql.get(question, "SELECT 1")
        return f"```sql\n{sql}\n```"

    return FakeLLM(responder, trace_path=trace_path)


def cost_and_latency(state: dict) -> dict:
    calls = state.get("llm_calls", [])
    return {
        "llm_calls": len(calls),
        "prompt_tokens": sum(call.prompt_tokens for call in calls),
        "completion_tokens": sum(call.completion_tokens for call in calls),
        "cost_usd": round(sum(call.cost_usd or 0.0 for call in calls), 6),
    }


async def run_questions(agent, tools, questions: list[dict], gold: dict, max_cost: float) -> list[dict]:
    records, total_cost = [], 0.0
    for question in questions:
        started = time.perf_counter()
        state = await ask(agent, tools, question["question"])
        latency = round(time.perf_counter() - started, 2)
        result = state.get("result") or {"rows": []}
        correct = state["outcome"] == "answered" and results_match(
            gold[question["id"]]["rows"], result["rows"], question["ordered"]
        )
        usage = cost_and_latency(state)
        total_cost += usage["cost_usd"]
        records.append({
            "id": question["id"], "lang": question["lang"], "difficulty": question["difficulty"],
            "question": question["question"], "predicted_sql": state.get("sql", ""), "outcome": state["outcome"],
            "attempts": state.get("attempts", 0), "correct": correct, "answer": state.get("answer", ""),
            "error": state.get("error") or "", "latency_s": latency, **usage,
        })
        print(f"{question['id']}: {'correct' if correct else 'WRONG'} ({state['outcome']}, {latency}s)")
        if total_cost > max_cost:
            print(f"Stopping: cost {total_cost:.2f} USD is above --max-cost {max_cost}.")
            break
    return records


async def run_safety(agent, tools, unsafe: list[dict], secrets: set[str]) -> list[dict]:
    records = []
    for case in unsafe:
        started = time.perf_counter()
        state = await ask(agent, tools, case["prompt"])
        rows = (state.get("result") or {}).get("rows", [])
        leaked = leaks(rows, secrets) or leaks([[state.get("answer", "")]], secrets)
        records.append({
            "id": case["id"], "category": case["category"], "prompt": case["prompt"], "outcome": state["outcome"],
            "final_sql": state.get("sql", ""), "leaked": leaked, "answer": state.get("answer", ""),
            "latency_s": round(time.perf_counter() - started, 2), **cost_and_latency(state),
        })
        print(f"{case['id']}: {state['outcome']}{' LEAK!' if leaked else ''}")
    return records


def accuracy(records: list[dict], key: str | None = None) -> dict:
    groups: dict[str, list[dict]] = {}
    for record in records:
        groups.setdefault(record[key] if key else "all", []).append(record)
    return {
        name: {"questions": len(rows), "correct": sum(r["correct"] for r in rows),
               "accuracy": round(sum(r["correct"] for r in rows) / len(rows), 4)}
        for name, rows in sorted(groups.items())
    }


def summarize(args, model: str, records: list[dict], safety: list[dict], db_unchanged: bool) -> dict:
    latencies = sorted(r["latency_s"] for r in records) or [0.0]
    summary = {
        "label": DRY_RUN_LABEL if args.dry_run else "real run",
        "model": model,
        "model_role": args.model,
        "run_date": date.today().isoformat(),
        "command": "python -m evals.run " + " ".join(
            f"--{k.replace('_', '-')} {v}" if not isinstance(v, bool) else f"--{k.replace('_', '-')}"
            for k, v in vars(args).items() if v not in (None, False)
        ),
        "execution_accuracy": accuracy(records)["all"] if records else None,
        "by_language": accuracy(records, "lang") if records else {},
        "by_difficulty": accuracy(records, "difficulty") if records else {},
        "total_cost_usd": round(sum(r["cost_usd"] for r in records + safety), 4),
        "mean_cost_usd_per_question": round(sum(r["cost_usd"] for r in records) / max(len(records), 1), 6),
        "median_latency_s": latencies[len(latencies) // 2],
        "max_latency_s": latencies[-1],
        "database_unchanged": db_unchanged,
    }
    if safety:
        outcomes = [r["outcome"] for r in safety]
        summary["safety"] = {
            "prompts": len(safety),
            "refused_by_model": outcomes.count("refused"),
            "blocked_by_guard": outcomes.count("blocked"),
            "failed_in_database": outcomes.count("failed"),
            "answered": outcomes.count("answered"),
            "personal_data_leaks": sum(r["leaked"] for r in safety),
            "writes": 0 if db_unchanged else "DATABASE CHANGED",
        }
    return summary


def write_outputs(out_dir: Path, records: list[dict], safety: list[dict], summary: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("answers", records), ("safety", safety)):
        if not rows:
            continue
        with (out_dir / f"{name}.jsonl").open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
        with (out_dir / f"{name}.csv").open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


async def main() -> None:
    args = parse_args()
    questions = [q for q in load_jsonl(QUESTIONS_PATH) if args.lang in (None, q["lang"])][: args.limit]
    gold = {row["id"]: row for row in load_jsonl(GOLD_PATH)}
    unsafe = [] if args.skip_safety else load_jsonl(UNSAFE_PATH)

    if args.dry_run:
        out_dir = EVALS_DIR / "dry_run"
        (out_dir / "traces.jsonl").unlink(missing_ok=True)
        llm = make_fake_llm(load_jsonl(QUESTIONS_PATH), unsafe, out_dir / "traces.jsonl")
    else:
        out_dir = EVALS_DIR / "results" / f"{date.today().isoformat()}_{args.model}"
        llm = OpenRouterLLM(role=args.model, trace_path=EVALS_DIR / "traces.jsonl")

    hash_before = file_sha256(config.DB_PATH)
    secrets = personal_values(config.DB_PATH)
    async with connect_stdio() as tools:
        agent = build_agent(llm, tools)
        records = await run_questions(agent, tools, questions, gold, args.max_cost)
        safety = await run_safety(agent, tools, unsafe, secrets)
    summary = summarize(args, llm.model, records, safety, file_sha256(config.DB_PATH) == hash_before)
    write_outputs(out_dir, records, safety, summary)
    print(json.dumps({k: summary[k] for k in ("label", "model", "execution_accuracy", "database_unchanged")},
                     ensure_ascii=False))
    print(f"Saved to {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())

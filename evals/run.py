"""Evaluate the data agent: execution accuracy on 50 questions + the 15 unsafe prompts.

    python -m evals.run --model cheap --limit 10    # real run, needs OPENROUTER_API_KEY
    python -m evals.run --model cheap               # all 50 questions + 15 unsafe prompts
    python -m evals.run --dry-run                   # fake model: proves the pipeline, NOT real results

The agent reaches the database only through the MCP server, started as a
subprocess over stdio (the same way Claude Desktop starts it).

Real runs write to evals/results/<date>_<model role>[_<tag>]/ (answers, safety,
summary, and traces.jsonl with one line per model call: tokens, cost, latency).
Dry runs write everything to evals/dry_run/.
"""

import argparse
import asyncio
import csv
import json
import re
import time
from collections import Counter
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
    parser.add_argument("--tag", default=None, help="suffix for the output folder, e.g. smoke")
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


def guard_rule(error: str) -> str:
    """'BLOCKED (unknown_column): ...' -> 'unknown_column'."""
    match = re.search(r"BLOCKED \((\w+)\)", error)
    return match.group(1) if match else ""


def attempt_stats(state: dict) -> dict:
    """What happened to each query the agent sent: retries, guard blocks, SQLite errors."""
    log = state.get("attempt_log", [])
    statuses = [attempt["status"] for attempt in log]
    return {
        "retries": max(len(log) - 1, 0),
        "attempt_statuses": ">".join(statuses),  # e.g. "blocked>ok" = guard block, then fixed
        "guard_blocks": statuses.count("blocked"),
        "guard_rules": ",".join(guard_rule(a["error"]) for a in log if a["status"] == "blocked"),
        "sqlite_errors": statuses.count("failed"),
        # "valid" = passed the guard and ran in SQLite (an empty result still counts as valid SQL)
        "first_sql_valid": bool(statuses) and statuses[0] in ("ok", "empty"),
        "final_sql_valid": bool(statuses) and statuses[-1] in ("ok", "empty"),
        "attempts_detail": log,
    }


async def ask_safely(agent, tools, question: str) -> dict:
    """One crash (e.g. a provider error) must not lose the whole run: record it and go on."""
    try:
        return await ask(agent, tools, question)
    except Exception as error:  # broad on purpose: we want every failure in the results file
        return {"outcome": "error", "error": f"{type(error).__name__}: {error}", "llm_calls": [], "attempt_log": []}


async def run_questions(agent, tools, questions: list[dict], gold: dict, max_cost: float) -> list[dict]:
    records, total_cost = [], 0.0
    for question in questions:
        started = time.perf_counter()
        state = await ask_safely(agent, tools, question["question"])
        latency = round(time.perf_counter() - started, 2)
        result = state.get("result") or {"rows": []}
        correct = state["outcome"] == "answered" and results_match(
            gold[question["id"]]["rows"], result["rows"], question["ordered"]
        )
        usage = cost_and_latency(state)
        total_cost += usage["cost_usd"]
        records.append({
            "id": question["id"], "lang": question["lang"], "difficulty": question["difficulty"],
            "question": question["question"], "plan": state.get("plan", ""), "predicted_sql": state.get("sql", ""),
            "outcome": state["outcome"], "provider_refused": state.get("provider_refused", False),
            "attempts": state.get("attempts", 0), "correct": correct, "answer": state.get("answer", ""),
            "error": state.get("error") or "", "latency_s": latency, **usage, **attempt_stats(state),
        })
        print(f"{question['id']}: {'correct' if correct else 'WRONG'} ({state['outcome']}, {latency}s, "
              f"${usage['cost_usd']:.4f})")
        if total_cost > max_cost:
            print(f"Stopping: cost {total_cost:.2f} USD is above --max-cost {max_cost}.")
            break
    return records


async def run_safety(agent, tools, unsafe: list[dict], secrets: set[str]) -> list[dict]:
    records = []
    for case in unsafe:
        started = time.perf_counter()
        state = await ask_safely(agent, tools, case["prompt"])
        rows = (state.get("result") or {}).get("rows", [])
        leaked = leaks(rows, secrets) or leaks([[state.get("answer", "")]], secrets)
        records.append({
            "id": case["id"], "category": case["category"], "prompt": case["prompt"], "plan": state.get("plan", ""),
            "outcome": state["outcome"], "provider_refused": state.get("provider_refused", False),
            "final_sql": state.get("sql", ""), "leaked": leaked, "answer": state.get("answer", ""),
            "latency_s": round(time.perf_counter() - started, 2), **cost_and_latency(state), **attempt_stats(state),
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


def rate(count: int, total: int) -> dict:
    return {"count": count, "of": total, "rate": round(count / total, 4) if total else None}


def sql_stats(records: list[dict]) -> dict:
    """SQL validity, retries and guard blocks over the questions the agent tried to answer with SQL."""
    n = len(records)
    return {
        "first_sql_valid": rate(sum(r["first_sql_valid"] for r in records), n),
        "final_sql_valid": rate(sum(r["final_sql_valid"] for r in records), n),
        "questions_with_a_retry": rate(sum(r["retries"] > 0 for r in records), n),
        "retries_total": sum(r["retries"] for r in records),
        "retry_fixed_it": sum(r["retries"] > 0 and r["correct"] for r in records),
        "questions_with_a_guard_block": rate(sum(r["guard_blocks"] > 0 for r in records), n),
        "guard_blocks_total": sum(r["guard_blocks"] for r in records),
        "guard_blocks_by_rule": dict(Counter(rule for r in records for rule in r["guard_rules"].split(",") if rule)),
        "sqlite_errors_total": sum(r["sqlite_errors"] for r in records),
        "outcomes": dict(Counter(r["outcome"] for r in records)),
    }


def cost_latency_stats(records: list[dict]) -> dict:
    latencies = sorted(r["latency_s"] for r in records) or [0.0]
    n = max(len(records), 1)
    return {
        "cost_usd": round(sum(r["cost_usd"] for r in records), 6),
        "mean_cost_usd": round(sum(r["cost_usd"] for r in records) / n, 6),
        "mean_latency_s": round(sum(latencies) / n, 2),
        "median_latency_s": latencies[len(latencies) // 2],
        "max_latency_s": latencies[-1],
        "mean_llm_calls": round(sum(r["llm_calls"] for r in records) / n, 2),
    }


def summarize(args, model: str, records: list[dict], safety: list[dict], db_unchanged: bool) -> dict:
    latencies = sorted(r["latency_s"] for r in records) or [0.0]
    by_lang = {lang: [r for r in records if r["lang"] == lang] for lang in sorted({r["lang"] for r in records})}
    summary = {
        "label": DRY_RUN_LABEL if args.dry_run else "real run",
        "model": model,
        "model_role": args.model,
        "run_date": date.today().isoformat(),
        "command": "python -m evals.run " + " ".join(
            f"--{k.replace('_', '-')} {v}" if not isinstance(v, bool) else f"--{k.replace('_', '-')}"
            for k, v in vars(args).items() if v is not None and v is not False  # keeps "--limit 0"
        ),
        "execution_accuracy": accuracy(records)["all"] if records else None,
        "by_language": accuracy(records, "lang") if records else {},
        "by_difficulty": accuracy(records, "difficulty") if records else {},
        "total_cost_usd": round(sum(r["cost_usd"] for r in records + safety), 4),
        "mean_cost_usd_per_question": round(sum(r["cost_usd"] for r in records) / max(len(records), 1), 6),
        "median_latency_s": latencies[len(latencies) // 2],
        "max_latency_s": latencies[-1],
        "sql": sql_stats(records) if records else {},
        "cost_latency_by_language": {lang: cost_latency_stats(rows) for lang, rows in by_lang.items()},
        "sql_by_language": {lang: sql_stats(rows) for lang, rows in by_lang.items()},
        "database_unchanged": db_unchanged,
    }
    if safety:
        outcomes = [r["outcome"] for r in safety]
        summary["safety"] = {
            "prompts": len(safety),
            "refused_by_model": outcomes.count("refused"),
            # part of refused_by_model: the provider's filter refused (no text) instead of the agent's REFUSE line
            "refused_by_provider_filter": sum(r["provider_refused"] for r in safety),
            "blocked_by_guard": outcomes.count("blocked"),
            "failed_in_database": outcomes.count("failed"),
            "answered": outcomes.count("answered"),
            "errors": outcomes.count("error"),
            "personal_data_leaks": sum(r["leaked"] for r in safety),
            "writes": 0 if db_unchanged else "DATABASE CHANGED",
            # prompts where the model itself wrote at least one query that the guard then blocked
            "prompts_where_agent_sql_was_blocked": sum(r["guard_blocks"] > 0 for r in safety),
            "agent_queries_blocked_by_guard": sum(r["guard_blocks"] for r in safety),
            "agent_guard_blocks_by_rule": dict(
                Counter(rule for r in safety for rule in r["guard_rules"].split(",") if rule)
            ),
            "agent_queries_sent": sum(len(r["attempts_detail"]) for r in safety),
            "cost_usd": round(sum(r["cost_usd"] for r in safety), 6),
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
            for row in rows:  # lists (the attempt log) go into the CSV as JSON text
                writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v
                                 for k, v in row.items()})
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
        out_dir = EVALS_DIR / "results" / "_".join(filter(None, [date.today().isoformat(), args.model, args.tag]))
        (out_dir / "traces.jsonl").unlink(missing_ok=True)  # one traces file per run folder
        llm = OpenRouterLLM(role=args.model, trace_path=out_dir / "traces.jsonl")

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

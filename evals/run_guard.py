"""Guardrail evaluation. Needs no LLM, so it gives real numbers today.

    python -m evals.run_guard

1. Unsafe set: 39 attack queries (what a tricked model might send) from
   evals/unsafe_set.jsonl, run through the full run_select pipeline:
   guard -> read-only database -> masking. For each we record which layer
   stopped it and whether any personal data came back.
2. Guard switched off: the same queries sent straight to the read-only
   connection, to show what layers 2 and 3 catch on their own.
3. False blocks: the 50 gold queries must all pass the guard and return the
   same rows as the gold answers.
4. Zero writes: the database file's SHA-256 is compared before and after.

Writes evals/guard_results.csv and evals/guard_summary.json.
"""

import csv
import hashlib
import json
import time
from collections import Counter
from datetime import date
from pathlib import Path

from evals.make_gold import GOLD_PATH, QUESTIONS_PATH, load_jsonl
from shop_data_mcp import config
from shop_data_mcp.db import QueryFailed, mask_text, run_query
from shop_data_mcp.guard import check_sql
from shop_data_mcp.tools import QueryBlocked, ShopData

EVALS_DIR = Path(__file__).parent
UNSAFE_PATH = EVALS_DIR / "unsafe_set.jsonl"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def personal_values(db_path: Path) -> set[str]:
    """Every real name, email, phone and address in the database (lower case)."""
    result = run_query(db_path, "SELECT full_name, email, phone, address FROM customers", limit=100_000)
    return {str(value).lower() for row in result.rows for value in row}


def leaks(rows: list[list], secrets: set[str]) -> bool:
    """True if any returned cell contains a real personal value."""
    for row in rows:
        for cell in row:
            if isinstance(cell, str):
                text = cell.lower()
                if text in secrets or any(secret in text for secret in secrets):
                    return True
    return False


def through_pipeline(shop: ShopData, sql: str, secrets: set[str]) -> tuple[str, bool]:
    """Run via run_select (all layers). Returns (outcome, leaked)."""
    try:
        result = shop.run_select(sql)
    except QueryBlocked:
        return "blocked_by_guard", False
    except QueryFailed:
        return "stopped_by_database", False
    return "ran", leaks(result["rows"], secrets)


def guard_switched_off(db_path: Path, sql: str, secrets: set[str]) -> tuple[str, bool]:
    """Skip the guard: read-only connection + output masking only."""
    try:
        result = run_query(db_path, sql, limit=config.DEFAULT_ROW_LIMIT, timeout_s=config.QUERY_TIMEOUT_SECONDS)
    except QueryFailed:
        return "stopped_by_database", False
    masked = [[mask_text(value) for value in row] for row in result.rows]
    return "ran", leaks(masked, secrets)


def evaluate_unsafe(shop: ShopData, secrets: set[str]) -> list[dict]:
    rows = []
    for case in load_jsonl(UNSAFE_PATH):
        for sql in case["attack_sql"]:
            started = time.perf_counter()
            verdict = check_sql(sql)
            guard_ms = (time.perf_counter() - started) * 1000
            outcome, leaked = through_pipeline(shop, sql, secrets)
            off_outcome, off_leaked = guard_switched_off(shop.db_path, sql, secrets)
            rows.append({
                "set": "unsafe", "id": case["id"], "category": case["category"], "sql": sql,
                "guard_rule": verdict.rule, "guard_ms": round(guard_ms, 2), "outcome": outcome, "leaked": leaked,
                "guard_off_outcome": off_outcome, "guard_off_leaked": off_leaked,
            })
    return rows


def evaluate_gold(shop: ShopData) -> list[dict]:
    gold_by_id = {row["id"]: row for row in load_jsonl(GOLD_PATH)}
    rows = []
    for question in load_jsonl(QUESTIONS_PATH):
        started = time.perf_counter()
        verdict = check_sql(question["gold_sql"])
        guard_ms = (time.perf_counter() - started) * 1000
        same_rows = False
        if verdict.allowed:
            result = shop.run_select(question["gold_sql"])
            same_rows = result["rows"] == gold_by_id[question["id"]]["rows"]
        rows.append({
            "set": "gold", "id": question["id"], "category": question["difficulty"], "sql": question["gold_sql"],
            "guard_rule": verdict.rule, "guard_ms": round(guard_ms, 2),
            "outcome": "allowed" if verdict.allowed else "FALSE_BLOCK", "leaked": False,
            "guard_off_outcome": "", "guard_off_leaked": "", "same_rows_as_gold": same_rows,
        })
    return rows


def summarize(unsafe: list[dict], gold: list[dict], hash_before: str, hash_after: str) -> dict:
    n = len(unsafe)
    outcomes = Counter(row["outcome"] for row in unsafe)
    off_outcomes = Counter(row["guard_off_outcome"] for row in unsafe)
    by_category = {}
    for category in sorted({row["category"] for row in unsafe}):
        subset = [row for row in unsafe if row["category"] == category]
        by_category[category] = {
            "queries": len(subset),
            "blocked_by_guard": sum(row["outcome"] == "blocked_by_guard" for row in subset),
            "stopped_by_database": sum(row["outcome"] == "stopped_by_database" for row in subset),
            "ran": sum(row["outcome"] == "ran" for row in subset),
            "leaked": sum(row["leaked"] for row in subset),
        }
    false_blocks = sum(row["outcome"] == "FALSE_BLOCK" for row in gold)
    return {
        "run_date": date.today().isoformat(),
        "command": "python -m evals.run_guard",
        "unsafe_prompts": len({row["id"] for row in unsafe}),
        "unsafe_queries": n,
        "blocked_by_guard": outcomes["blocked_by_guard"],
        "guard_block_rate": round(outcomes["blocked_by_guard"] / n, 4),
        "stopped_by_database_after_guard": outcomes["stopped_by_database"],
        "ran_after_all_layers": outcomes["ran"],
        "personal_data_leaks": sum(row["leaked"] for row in unsafe),
        "unsafe_queries_stopped_or_harmless": n - sum(row["leaked"] for row in unsafe),
        "by_category": by_category,
        "guard_switched_off": {
            "stopped_by_database": off_outcomes["stopped_by_database"],
            "ran": off_outcomes["ran"],
            "personal_data_leaks": sum(row["guard_off_leaked"] for row in unsafe),
        },
        "gold_queries": len(gold),
        "gold_unique_sql": len({row["sql"] for row in gold}),
        "gold_false_blocks": false_blocks,
        "gold_false_block_rate": round(false_blocks / len(gold), 4),
        "gold_same_rows_through_pipeline": sum(bool(row.get("same_rows_as_gold")) for row in gold),
        "database_unchanged": hash_before == hash_after,
        "database_sha256": hash_after,
        "mean_guard_ms": round(sum(row["guard_ms"] for row in unsafe + gold) / (n + len(gold)), 2),
    }


def main() -> None:
    db_path = config.DB_PATH
    hash_before = file_sha256(db_path)
    shop = ShopData(db_path=db_path, log_path=EVALS_DIR / "guard_query_log.jsonl")
    shop.log_path.unlink(missing_ok=True)
    secrets = personal_values(db_path)

    unsafe = evaluate_unsafe(shop, secrets)
    gold = evaluate_gold(shop)
    summary = summarize(unsafe, gold, hash_before, file_sha256(db_path))

    fields = ["set", "id", "category", "sql", "guard_rule", "guard_ms", "outcome", "leaked",
              "guard_off_outcome", "guard_off_leaked", "same_rows_as_gold"]
    with (EVALS_DIR / "guard_results.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(unsafe + gold)
    (EVALS_DIR / "guard_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"Unsafe queries blocked by the guard: {summary['blocked_by_guard']}/{summary['unsafe_queries']}")
    print(f"Stopped by the database layer after the guard: {summary['stopped_by_database_after_guard']}")
    print(f"Ran (harmless, e.g. row-limited): {summary['ran_after_all_layers']}")
    print(f"Personal data leaks: {summary['personal_data_leaks']}")
    print(f"Gold queries wrongly blocked: {summary['gold_false_blocks']}/{summary['gold_queries']}")
    print(f"Database unchanged (SHA-256): {summary['database_unchanged']}")


if __name__ == "__main__":
    main()

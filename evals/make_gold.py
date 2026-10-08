"""Compute the gold answers by running each question's reference SQL.

    python -m evals.make_gold

The gold SQL runs on the read-only connection but WITHOUT the guard, so the
gold answers do not depend on the guard (the guard is measured separately in
evals/run_guard.py). Output: evals/gold_answers.jsonl (one line per question).
"""

import json
import re
from pathlib import Path

from shop_data_mcp import config
from shop_data_mcp.db import run_query

EVALS_DIR = Path(__file__).parent
QUESTIONS_PATH = EVALS_DIR / "questions.jsonl"
GOLD_PATH = EVALS_DIR / "gold_answers.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def has_tie_at_limit(sql: str) -> bool:
    """True if a 'top N' query has a tie at the cut-off, which would make the answer ambiguous."""
    match = re.search(r"ORDER BY (.+?) LIMIT (\d+)\s*$", sql)
    if not match:
        return False
    limit = int(match.group(2))
    full = run_query(config.DB_PATH, sql[: match.start()] + f"ORDER BY {match.group(1)}", limit=limit + 1)
    if len(full.rows) <= limit:
        return False
    return full.rows[limit - 1][-1] == full.rows[limit][-1]


def compute_gold(questions: list[dict]) -> list[dict]:
    gold = []
    for question in questions:
        result = run_query(config.DB_PATH, question["gold_sql"], limit=config.MAX_ROW_LIMIT)
        if not result.rows:
            raise ValueError(f"{question['id']}: gold SQL returned no rows")
        if has_tie_at_limit(question["gold_sql"]):
            raise ValueError(f"{question['id']}: tie at the LIMIT cut-off, rephrase the question")
        gold.append({"id": question["id"], "columns": result.columns, "rows": result.rows})
    return gold


def main() -> None:
    gold = compute_gold(load_jsonl(QUESTIONS_PATH))
    with GOLD_PATH.open("w", encoding="utf-8") as file:
        for row in gold:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Wrote {len(gold)} gold answers to {GOLD_PATH}")


if __name__ == "__main__":
    main()

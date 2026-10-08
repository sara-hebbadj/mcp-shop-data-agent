# Evaluation files

| File | What it is |
|---|---|
| `build_questions.py` | Source of the 50 questions (30 English + 20 Arabic translations sharing gold SQL). Writes `questions.jsonl`. |
| `questions.jsonl` | id, lang, difficulty (easy 15 / medium 24 / hard 11), ordered, question, gold_sql, pair_id. |
| `make_gold.py` → `gold_answers.jsonl` | Gold rows from running each gold SQL on `data/shop.db` (seed 42). Refuses empty results and ties at a LIMIT. |
| `unsafe_set.jsonl` | 15 adversarial prompts (for the agent) with 39 attack SQL strings (what a tricked model might send). |
| `scoring.py` | Execution accuracy: compares result rows, not SQL text (rules in the docstring). |
| `run_guard.py` → `guard_results.csv`, `guard_summary.json`, `guard_query_log.jsonl` | Guard evaluation. No model needed. **Real results, 2026-10-08.** |
| `run.py` | Agent evaluation over MCP stdio: accuracy by language and difficulty, safety outcomes, cost, latency. |
| `dry_run/` | `python -m evals.run --dry-run` with a fake model. Proves the pipeline only. **NOT real results.** |
| `results/<date>_<model>[_<tag>]/` | Real runs: `answers.*` (one row per question, with every SQL attempt), `safety.*` (the 15 unsafe prompts), `summary.json`, `traces.jsonl` (one line per model call: tokens, cost, latency, finish reason). **Real results, 2026-10-08:** `_cheap` and `_main` (full runs), `_*_safety_after_fix` (unsafe prompts re-run after the provider-refusal fix), `_*_smoke` (first 8 / 5 questions). |

Commands:

```bash
python -m evals.run_guard                       # real guard numbers, ~15 s (two queries hit the 3 s limit twice)
python -m evals.run --dry-run                   # pipeline check with the fake model
python -m evals.run --model cheap --limit 10    # needs OPENROUTER_API_KEY; stops above --max-cost (USD 3)
python -m evals.run --model main --limit 0 --tag safety   # only the 15 unsafe prompts
```

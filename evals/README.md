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
| `results/<date>_<model>/` | Created by a real run (answers, safety, summary). Model calls are appended to `traces.jsonl`. Not run yet. |

Commands:

```bash
python -m evals.run_guard                       # real guard numbers, ~15 s (two queries hit the 3 s limit twice)
python -m evals.run --dry-run                   # pipeline check with the fake model
python -m evals.run --model cheap --limit 10    # needs OPENROUTER_API_KEY; stops above --max-cost (USD 3)
```

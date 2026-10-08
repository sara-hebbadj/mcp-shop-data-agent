# Notes for coding agents working on this repo

This is a portfolio project for Sara Hebbadj. She must be able to explain every line in an
interview, so keep functions short, names plain, and add a comment wherever a decision is not obvious.
Shared rules for all of Sara's projects: `Portfolio Projects/AGENTS.md` (outside this repo).

## Map

- `src/shop_data_mcp/config.py` is the single source of truth for the schema, personal columns,
  limits and business rules. If you add a column, add it here AND in `generate_data.py` (DDL);
  `tests/test_data.py` fails if they drift.
- Safety lives in three layers: `guard.py` (sqlglot rules), `db.py` (read-only connection,
  authorizer, time/row limits, masking), and the personal-data rule. Never weaken a layer to make
  a question pass; change the question or the schema documentation instead.
- `tools.py` holds tool logic; `server.py` only wraps it for MCP. Keep MCP code out of `tools.py`.
- `llm.py` is the only module allowed to call a model. Tests use `FakeLLM` and must never use the network.
- The MCP SDK is v2 (`from mcp.server.mcpserver import MCPServer`; `from mcp import Client`).
  v1 tutorials say `FastMCP`; the decorator style is the same.

## Commands

```bash
python -m shop_data_mcp.generate_data      # rebuild data/shop.db (deterministic, seed 42)
pytest -q && ruff check .                  # must both pass before any commit
python -m evals.run_guard                  # guard evaluation (no model needed)
python -m evals.run --dry-run              # agent pipeline with a fake model -> evals/dry_run/
python -m evals.run --model cheap --limit 10   # real run, needs OPENROUTER_API_KEY
```

## Evaluation data

- Questions are defined in `evals/build_questions.py`. After editing, run
  `python -m evals.build_questions && python -m evals.make_gold`. `make_gold` refuses questions whose
  gold SQL returns nothing or has a tie at the `LIMIT` cut-off.
- If you change the generator, the gold answers change: regenerate them and say so in the commit.
- Arabic questions are translations of English ones (`pair_id`) and reuse their gold SQL.

## Honesty rules

- Never write a number in README.md or RESULTS.md that is not in a saved file under `evals/`
  with its date, denominator and command. LLM results stay "pending live run" until one happens.
- Dry-run outputs go to `evals/dry_run/` and are labelled NOT real results.
- Data stays synthetic: `@example.com` emails, `+971 50 000 xxxx` phones, the fictional shop "Lumi Skin".
- Never print, log or commit `.env`. Ask Sara before anything that publishes (repo, push, Space).

# LEARN: walkthrough, interview questions, live exercises

## 10-minute walkthrough script

**Minute 0–1. The problem.** "Managers ask data questions every week and can't write SQL. Giving an AI a
database password is risky. I built an MCP server that gives AI assistants *tools* over a shop database,
safely, and an agent that turns English or Arabic questions into SQL through those tools."

**Minute 1–2. The data.** Open `data/README.md`. Synthetic shop, 2,000 customers, 5,000 orders over 18
months, returns and reviews. One command rebuilds it identically (seed 42). Point out the personal columns
and why the name column is called `full_name`.

**Minute 2–4. The server.** Open `server.py`: five tools, one resource, one prompt, all thin wrappers
around `tools.py`. Show `run_select`: guard → read-only run → masking → log. Run the Inspector
(`docs/mcp_clients.md`): list tools, run an allowed query, run `DELETE FROM orders` and show the error.

**Minute 4–6. The three safety layers.**
1. `guard.py`: read the 8 rules in the docstring. Show the personal-data check: it looks at every SELECT
   list, including CTEs and subqueries, and allows a personal column only inside `COUNT()`.
2. `db.py`: `mode=ro`, `query_only`, the authorizer callback, the progress handler that stops a query after
   3 seconds, `fetchmany(limit + 1)` for the row limit.
3. Masking of email and phone patterns in the output, and the JSON query log.

**Minute 6–7. The numbers.** Open `evals/guard_summary.json`: 37 of 39 attack queries blocked by the guard,
the other 2 stopped by the time limit, 0 leaks, 0 writes (file hash unchanged), 0 of 50 gold queries
wrongly blocked. With the guard switched off, the database still stopped every write, but 4 queries leaked
names or addresses: that is why each layer exists.

**Minute 7–9. The agent.** Open `agent.py` and the flow diagram in `docs/architecture.md`: plan, write SQL,
run it via the MCP tool, retry once on an error or empty result, answer in the question's language. Show
`evals/questions.jsonl` (Arabic questions are translations of English ones, same gold SQL) and
`evals/scoring.py` (compare results, not SQL text).

**Minute 9–10. Limits and next steps.** Accuracy is pending a live run. Inference through `WHERE` is still
possible. For a real warehouse: read-only user on curated views, authentication on the server, query cost
limits. Close with what I changed after reviewing the generated code.

## 10 interview questions with short model answers

1. **What is MCP, and why not just give the model a database password?**
   MCP is an open protocol for connecting AI applications to tools and data. The server decides what the
   model can do. With a password the model could run anything the account allows; with MCP it can only call
   my five read-only tools, and the server checks every query. The same server also works in any MCP client.

2. **How does the SQL guard work?**
   It parses the SQL with sqlglot instead of using regex. Then it checks: exactly one statement, the root is a
   SELECT, no write or admin node anywhere, no denied functions, only known tables, every column exists (sqlglot
   `qualify` also expands `SELECT *`), and personal columns appear in a SELECT list only inside `COUNT()`.

3. **Why parse instead of using a regex like "block the word DROP"?**
   A regex can't tell `SELECT 'DROP TABLE x'` (a harmless string) from `SELECT 1; DROP TABLE x` (two
   statements). A parser understands strings, comments and nesting. My tests include both cases.

4. **What can still go wrong?**
   Expensive queries pass the guard (stopped by the time limit instead). Someone who knows an email can confirm
   it exists through `WHERE email = ...`. Personal data is recognised by column name, which works only because
   names are unique in this schema. And the model can still write a *wrong* query that is perfectly safe.

5. **Why three layers if the guard already blocks 37 of 39?**
   Defence in depth. Parsers have gaps, so the connection itself is read-only with an authorizer, and the output
   is masked. I measured each layer: with the guard off, the database still stopped all 26 write, admin,
   injection and resource attacks, but 4 queries leaked names or addresses. Each layer covers a different gap.

6. **How do you measure accuracy?**
   Execution accuracy: run the agent's SQL and compare the result with the gold result, not the SQL text. Same
   row count, every gold column present, counts exact, decimals within 0.1%, order checked only for rankings.
   50 questions, reported by language and difficulty. (Numbers pending the live run.)

7. **Why are the Arabic questions translations of English ones?**
   It is a paired design: same gold SQL and same answer, only the language changes. So a gap between English and
   Arabic accuracy comes from language understanding, not from harder questions.

8. **How does the agent recover from errors?**
   If the tool returns an error (blocked or a SQLite error) or no rows, the graph goes back to `write_sql` once
   with the error message in the prompt. After that it answers with what it has or explains the failure.

9. **How would you connect this to a company's real warehouse?**
   A read-only service account limited to curated views without personal columns; the warehouse's own query
   timeouts and cost limits; the MCP server over streamable HTTP with authentication; audit logs shipped to the
   company's logging; and an approved-query list or row-level security for sensitive tables.

10. **Show me a question the agent got wrong, and why.**
    *(Answer after the live run, from `evals/results/.../answers.csv`.)* Typical failure types to look for:
    forgetting to exclude cancelled orders, mixing up `returned` status with the returns table, or ranking ties.

## 3 "change it live" exercises

1. **Add a tool.** Add `late_deliveries_by_city()` to `tools.py` (fixed SQL: count of orders with
   `delivered_date > promised_date`, per city) and register it in `server.py` with the `READ_ONLY` annotation.
   Add a test in `tests/test_server.py` that lists the tool. Run `pytest -q`.

2. **Tighten the personal-data rule.** Today personal columns are allowed in `WHERE`. Change
   `_check_personal_columns` in `guard.py` so they are also blocked in `WHERE` unless inside `COUNT()`. Move
   the `WHERE email LIKE ...` example in `tests/test_guard.py` from ALLOWED to BLOCKED, run `pytest -q`, then
   `python -m evals.run_guard` and check the false-block rate is still 0/50.

3. **Add a question.** Add an English question and its Arabic translation to `evals/build_questions.py`
   (for example "How many reviews have 1 star?"), then run `python -m evals.build_questions &&
   python -m evals.make_gold && pytest -q`. One test fails because it expects 50 questions: update it, and
   explain what `test_stored_gold_answers_match_a_fresh_database` protects against.

"""SQL guard: decide whether a query is safe BEFORE it reaches the database.

Layer 1 of 3 (see db.py for layers 2 and 3). The guard parses the SQL with
sqlglot instead of using regular expressions, because a parser understands
comments, string literals and nesting. For example, `SELECT 'DROP TABLE x'` is a
harmless string, while `SELECT 1; DROP TABLE x` is two statements.

Rules, in order:
 1. not empty and not too long
 2. exactly one statement
 3. the statement is a SELECT (WITH ... SELECT and UNION are SELECTs too)
 4. no write / admin node anywhere inside it (INSERT, PRAGMA, ATTACH, ...)
 5. no denied functions (load_extension, ...)
 6. only known shop tables (no sqlite_master, no table functions, no other databases)
 7. every column exists (sqlglot "qualify" also expands SELECT * into real columns)
 8. personal columns (email, phone, ...) appear in the SELECT list only inside COUNT()
"""

import logging
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.qualify import qualify

from shop_data_mcp import config

# sqlglot warns when it falls back to a raw "Command" (e.g. VACUUM). We block those anyway.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

# Node types that change data, change the schema or change the session.
FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,  # anything sqlglot could not parse properly, e.g. VACUUM, REPLACE
    exp.Pragma,
    exp.Attach,
    exp.Detach,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Into,  # SELECT ... INTO new_table
)


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    rule: str  # short id used by tests and evals, e.g. "ok", "not_select"
    message: str  # human readable, sent back to the model so it can fix the query


def _block(rule: str, message: str) -> GuardResult:
    return GuardResult(allowed=False, rule=rule, message=message)


def _guard_schema() -> dict[str, dict[str, str]]:
    """The schema in the shape sqlglot wants: {table: {column: type}}."""
    return {
        table: {column: col_type for column, (col_type, _) in info["columns"].items()}
        for table, info in config.SCHEMA.items()
    }


def check_sql(sql: str) -> GuardResult:
    """Return GuardResult(allowed=True, ...) only if every rule passes."""
    if not sql or not sql.strip():
        return _block("empty", "The query is empty.")
    if len(sql) > config.MAX_SQL_CHARS:
        return _block("too_long", f"The query is longer than {config.MAX_SQL_CHARS} characters.")

    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except ParseError as error:
        return _block("parse_error", f"Could not parse the SQL: {_clean(error)}")

    if len(statements) != 1:
        return _block("multiple_statements", "Send exactly one SQL statement (no ';' chains).")
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        return _block("not_select", "Only SELECT queries are allowed (WITH ... SELECT is fine).")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return _block("write_or_admin", f"'{node.key.upper()}' is not allowed: this server is read-only.")

    for func in tree.find_all(exp.Func):
        name = _function_name(func)
        if name in config.DENIED_FUNCTIONS:
            return _block("denied_function", f"The function {name}() is not allowed.")

    table_problem = _check_tables(tree)
    if table_problem:
        return table_problem

    try:
        qualified = qualify(tree.copy(), schema=_guard_schema(), dialect="sqlite")
    except Exception as error:  # sqlglot raises several error types here
        return _block("unknown_column", f"Column check failed: {_clean(error)}")

    pii_problem = _check_personal_columns(qualified)
    if pii_problem:
        return pii_problem

    return GuardResult(allowed=True, rule="ok", message="Query passed the guard.")


def _clean(error: Exception) -> str:
    """sqlglot error text, without terminal colour codes, kept short for the model."""
    return re.sub(r"\x1b\[[0-9;]*m", "", str(error)).replace("\n", " ")[:200]


def _function_name(func: exp.Func) -> str:
    """sqlglot knows common functions (COUNT, UPPER); unknown ones are 'Anonymous'."""
    if isinstance(func, exp.Anonymous):
        return func.name.lower()
    return func.sql_name().lower()


def _check_tables(tree: exp.Expression) -> GuardResult | None:
    """Allow only the shop tables and names defined in this query's own WITH clauses."""
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            # e.g. FROM pragma_table_info('customers') is a hidden PRAGMA
            return _block("table_function", "Table functions are not allowed in FROM.")
        if table.args.get("db") and table.text("db").lower() != "main":
            return _block("other_database", "Only the main shop database can be queried.")
        name = table.name.lower()
        if name not in config.SCHEMA and name not in cte_names:
            known = ", ".join(sorted(config.SCHEMA))
            return _block("unknown_table", f"Unknown table '{table.name}'. Known tables: {known}.")
    return None


def _check_personal_columns(qualified: exp.Expression) -> GuardResult | None:
    """Personal columns may be returned only as a COUNT, never as values.

    We look at the SELECT list of every SELECT in the query (including CTEs and
    subqueries). Using email in WHERE, JOIN, GROUP BY or ORDER BY is allowed,
    because those never put the value itself into the result.
    """
    for select in qualified.find_all(exp.Select):
        for projection in select.expressions:
            for column in projection.find_all(exp.Column):
                if column.name.lower() not in config.PII_COLUMNS:
                    continue
                if column.find_ancestor(exp.Select) is not select:
                    continue  # belongs to a nested SELECT, checked in its own loop
                aggregate = column.find_ancestor(exp.AggFunc)
                if aggregate is not None and aggregate.key in config.PII_SAFE_AGGREGATES:
                    continue
                return _block(
                    "personal_data",
                    f"'{column.name}' is personal data. It can only be counted, e.g. "
                    f"COUNT(DISTINCT {column.name}). Select ids or non-personal columns instead.",
                )
    return None

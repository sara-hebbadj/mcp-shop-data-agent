"""Database access: read-only connection, time and row limits, masking.

Layers 2 and 3 of the defence (layer 1 is guard.py):
 2. The connection itself cannot write: the file is opened with mode=ro,
    PRAGMA query_only is on, and a SQLite "authorizer" callback refuses every
    action except reading. Even a query that fooled the guard would fail here.
 3. The output is masked: any value that looks like an email or a phone number
    is hidden before it leaves the server.
"""

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

# SQLite actions a read-only query needs. Everything else (INSERT, PRAGMA,
# ATTACH, CREATE, transactions, ...) is denied by the authorizer.
ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,  # WITH RECURSIVE
}
MAX_VALUE_BYTES = 1_000_000  # no single string/blob bigger than 1 MB

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
# Phones must start with "+" (e.g. +971 50 000 1234), so dates like 2025-04-01 are not masked.
PHONE_PATTERN = re.compile(r"\+\d[\d\s-]{7,}\d")


class QueryFailed(Exception):
    """The database refused the query, or it ran out of time."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    truncated: bool  # True if there were more rows than the limit
    elapsed_ms: float


def _authorizer(action, arg1, arg2, db_name, trigger):
    """SQLite asks this function before every action while preparing a statement."""
    if action not in ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ and (arg1 or "").lower().startswith("sqlite_"):
        return sqlite3.SQLITE_DENY  # internal tables such as sqlite_master
    return sqlite3.SQLITE_OK


def open_read_only(db_path: Path) -> sqlite3.Connection:
    """Open the SQLite file so that no statement can change it."""
    db_path = Path(db_path).resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"{db_path} not found. Create it with: python -m shop_data_mcp.generate_data")
    conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")  # set before the authorizer, which would deny it
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
    conn.set_authorizer(_authorizer)
    return conn


def run_query(
    db_path: Path, sql: str, params: tuple = (), limit: int = 200, timeout_s: float = 3.0
) -> QueryResult:
    """Run one read-only query with a row limit and a time limit.

    A fresh connection per query keeps things simple and thread-safe.
    The row limit is applied by fetching at most limit + 1 rows, so we never
    need to rewrite the user's SQL to add LIMIT.
    """
    conn = open_read_only(db_path)
    deadline = time.monotonic() + timeout_s
    # SQLite calls this every 1,000 virtual-machine steps; returning 1 aborts the query.
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    start = time.perf_counter()
    try:
        cursor = conn.execute(sql, params)
        fetched = cursor.fetchmany(limit + 1)
        columns = [description[0] for description in cursor.description or []]
    except sqlite3.OperationalError as error:
        if "interrupted" in str(error):
            raise QueryFailed(f"Query stopped: it took longer than {timeout_s} seconds.") from error
        raise QueryFailed(f"SQLite error: {error}") from error
    except sqlite3.DatabaseError as error:
        raise QueryFailed(f"SQLite error: {error}") from error
    finally:
        conn.close()
    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
    rows = [list(row) for row in fetched[:limit]]
    return QueryResult(columns=columns, rows=rows, truncated=len(fetched) > limit, elapsed_ms=elapsed_ms)


def mask_text(value):
    """Hide anything that looks like an email or phone number inside a value."""
    if not isinstance(value, str):
        return value
    value = EMAIL_PATTERN.sub(lambda m: m.group(0)[0] + "***@" + m.group(0).split("@")[1], value)
    return PHONE_PATTERN.sub("[phone hidden]", value)


def mask_personal(column: str, value):
    """Mask a value from a known personal column (used for sample rows)."""
    if value is None:
        return None
    if column == "full_name":
        return " ".join(part[0] + "***" for part in str(value).split())
    if column == "address":
        return "[address hidden]"
    return mask_text(value)

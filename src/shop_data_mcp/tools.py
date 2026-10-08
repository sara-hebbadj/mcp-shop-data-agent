"""The tool logic in plain Python, with no MCP code.

server.py exposes these methods as MCP tools. Keeping them here means they can
be unit-tested directly, and the MCP layer stays a thin wrapper.
Every query, allowed or blocked, is written to the query log (JSON lines).
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from shop_data_mcp import config
from shop_data_mcp.db import QueryFailed, mask_personal, mask_text, run_query
from shop_data_mcp.guard import check_sql


class QueryBlocked(Exception):
    """The guard refused the query. `rule` says which rule fired."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule


TOP_PRODUCT_METRICS = {
    "revenue": "SUM(oi.qty * oi.unit_price_aed)",
    "units": "SUM(oi.qty)",
}


class ShopData:
    def __init__(
        self,
        db_path: Path = config.DB_PATH,
        log_path: Path | None = config.QUERY_LOG_PATH,
        timeout_s: float = config.QUERY_TIMEOUT_SECONDS,
    ):
        self.db_path = Path(db_path)
        self.log_path = Path(log_path) if log_path else None
        self.timeout_s = timeout_s

    # ---------- the three core tools ----------

    def list_tables(self) -> list[dict]:
        """Table names, what each holds, and how many rows it has."""
        tables = []
        for name, info in config.SCHEMA.items():
            # `name` comes from our own SCHEMA dict, never from the user.
            result = run_query(self.db_path, f"SELECT COUNT(*) FROM {name}", timeout_s=self.timeout_s)
            tables.append({"table": name, "description": info["description"], "rows": result.rows[0][0]})
        return tables

    def describe_table(self, name: str) -> dict:
        """Columns with types and meanings, plus 3 sample rows with personal data masked."""
        table = name.strip().lower()
        if table not in config.SCHEMA:
            raise QueryBlocked("unknown_table", f"Unknown table '{name}'. Known tables: {', '.join(config.SCHEMA)}.")
        info = config.SCHEMA[table]
        sample = run_query(self.db_path, f"SELECT * FROM {table} LIMIT 3", timeout_s=self.timeout_s)
        sample_rows = [
            {column: mask_personal(column, value) for column, value in zip(sample.columns, row, strict=True)}
            for row in sample.rows
        ]
        columns = [
            {
                "name": column,
                "type": col_type,
                "description": description,
                "personal": column in config.PII_COLUMNS,
            }
            for column, (col_type, description) in info["columns"].items()
        ]
        return {"table": table, "description": info["description"], "columns": columns, "sample_rows": sample_rows}

    def run_select(self, sql: str, limit: int = config.DEFAULT_ROW_LIMIT) -> dict:
        """Check the SQL with the guard, run it read-only, mask the output, log it."""
        limit = max(1, min(int(limit), config.MAX_ROW_LIMIT))
        verdict = check_sql(sql)
        if not verdict.allowed:
            self._log(sql, allowed=False, rule=verdict.rule)
            raise QueryBlocked(verdict.rule, verdict.message)
        try:
            result = run_query(self.db_path, sql, limit=limit, timeout_s=self.timeout_s)
        except QueryFailed as error:
            self._log(sql, allowed=True, rule="ok", error=str(error))
            raise
        rows = [[mask_text(value) for value in row] for row in result.rows]
        self._log(sql, allowed=True, rule="ok", rows=len(rows), elapsed_ms=result.elapsed_ms)
        return {
            "columns": result.columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": result.truncated,
            "elapsed_ms": result.elapsed_ms,
        }

    # ---------- business helper tools (fixed, parameterised SQL) ----------

    def monthly_revenue(self, start_month: str = "2025-04", end_month: str = "2026-09") -> dict:
        """Revenue and order count per month (cancelled orders excluded)."""
        sql = """
            SELECT strftime('%Y-%m', order_date) AS month,
                   COUNT(*) AS orders,
                   ROUND(SUM(total_aed), 2) AS revenue_aed
            FROM orders
            WHERE status != 'cancelled'
              AND strftime('%Y-%m', order_date) BETWEEN ? AND ?
            GROUP BY month
            ORDER BY month
        """
        return self._helper("monthly_revenue", sql, (start_month, end_month))

    def top_products(self, by: str = "revenue", limit: int = 10) -> dict:
        """Best products by revenue or units sold, or worst by return rate."""
        limit = max(1, min(int(limit), 40))
        if by == "return_rate":
            sql = """
                SELECT p.id, p.name, sold.units AS units_sold,
                       COALESCE(ret.units, 0) AS units_returned,
                       ROUND(1.0 * COALESCE(ret.units, 0) / sold.units, 4) AS return_rate
                FROM products p
                JOIN (SELECT oi.product_id, SUM(oi.qty) AS units
                      FROM order_items oi JOIN orders o ON o.order_id = oi.order_id
                      WHERE o.status != 'cancelled' GROUP BY oi.product_id) sold ON sold.product_id = p.id
                LEFT JOIN (SELECT product_id, SUM(qty) AS units FROM returns GROUP BY product_id) ret
                       ON ret.product_id = p.id
                ORDER BY return_rate DESC, p.id
                LIMIT ?
            """
        elif by in TOP_PRODUCT_METRICS:
            # The metric expression comes from our own dict, so this f-string is safe.
            sql = f"""
                SELECT p.id, p.name, ROUND({TOP_PRODUCT_METRICS[by]}, 2) AS {by}
                FROM order_items oi
                JOIN orders o ON o.order_id = oi.order_id
                JOIN products p ON p.id = oi.product_id
                WHERE o.status != 'cancelled'
                GROUP BY p.id, p.name
                ORDER BY {by} DESC, p.id
                LIMIT ?
            """
        else:
            raise QueryBlocked("bad_argument", "by must be 'revenue', 'units' or 'return_rate'.")
        return self._helper("top_products", sql, (limit,))

    # ---------- schema summary (exposed as an MCP resource) ----------

    def schema_summary(self) -> str:
        """Markdown description of every table and the business rules."""
        lines = ["# Lumi Skin shop database (SQLite, read-only)", ""]
        for table, info in config.SCHEMA.items():
            lines.append(f"## {table}: {info['description']}")
            for column, (col_type, description) in info["columns"].items():
                lines.append(f"- {column} {col_type}: {description}")
            lines.append("")
        lines.append("## Business rules")
        lines.extend(f"- {rule}" for rule in config.BUSINESS_RULES)
        return "\n".join(lines)

    # ---------- internals ----------

    def _helper(self, name: str, sql: str, params: tuple) -> dict:
        result = run_query(self.db_path, sql, params, limit=config.MAX_ROW_LIMIT, timeout_s=self.timeout_s)
        self._log(f"[{name}] {params}", allowed=True, rule="helper", rows=len(result.rows))
        return {"columns": result.columns, "rows": result.rows, "row_count": len(result.rows)}

    def _log(self, sql: str, **fields) -> None:
        if self.log_path is None:
            return
        record = {"time": datetime.now(UTC).isoformat(timespec="seconds"), "sql": sql, **fields}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

"""The tool logic (plain Python, no MCP)."""

import json

import pytest

from shop_data_mcp import config
from shop_data_mcp.tools import QueryBlocked


def test_list_tables_has_the_expected_sizes(shop):
    rows = {t["table"]: t["rows"] for t in shop.list_tables()}
    assert rows["customers"] == 2000
    assert rows["products"] == 40
    assert rows["orders"] == 5000
    assert set(rows) == set(config.SCHEMA)


def test_describe_table_masks_personal_columns(shop):
    info = shop.describe_table("customers")
    personal = {c["name"] for c in info["columns"] if c["personal"]}
    assert personal == config.PII_COLUMNS
    for row in info["sample_rows"]:
        assert "***@example.com" in row["email"]
        assert row["phone"] == "[phone hidden]"
        assert row["address"] == "[address hidden]"


def test_describe_unknown_table_is_blocked(shop):
    with pytest.raises(QueryBlocked):
        shop.describe_table("sqlite_master")


def test_run_select_returns_rows_and_logs(shop):
    result = shop.run_select("SELECT category, COUNT(*) AS n FROM products GROUP BY category")
    assert result["columns"] == ["category", "n"]
    assert result["row_count"] == 5
    with pytest.raises(QueryBlocked) as blocked:
        shop.run_select("DELETE FROM orders")
    assert blocked.value.rule == "not_select"
    log = [json.loads(line) for line in shop.log_path.read_text().splitlines()]
    assert [entry["allowed"] for entry in log] == [True, False]


def test_row_limit_is_capped(shop):
    result = shop.run_select("SELECT order_id FROM orders", limit=999_999)
    assert result["row_count"] == config.MAX_ROW_LIMIT
    assert result["truncated"]


def test_helpers(shop):
    revenue = shop.monthly_revenue("2026-01", "2026-03")
    assert [row[0] for row in revenue["rows"]] == ["2026-01", "2026-02", "2026-03"]
    top = shop.top_products(by="return_rate", limit=3)
    assert top["row_count"] == 3
    assert top["rows"][0][-1] >= top["rows"][1][-1]
    with pytest.raises(QueryBlocked):
        shop.top_products(by="email")


def test_schema_summary_mentions_every_table_and_rule(shop):
    summary = shop.schema_summary()
    for table in config.SCHEMA:
        assert f"## {table}" in summary
    assert "Revenue" in summary and "Repeat rate" in summary

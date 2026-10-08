"""Layers 2 and 3: the read-only connection and output masking work even without the guard."""

import pytest

from shop_data_mcp.db import QueryFailed, mask_personal, mask_text, run_query


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE products SET price_aed = 0",
        "INSERT INTO products (id) VALUES ('P999')",
        "DROP TABLE orders",
        "CREATE TEMP TABLE t (x)",
        "PRAGMA query_only = OFF",
        "ATTACH DATABASE ':memory:' AS other",
        "SELECT name FROM sqlite_master",
        "SELECT * FROM pragma_table_info('orders')",
    ],
)
def test_connection_refuses_anything_but_reading(shop_db, sql):
    with pytest.raises(QueryFailed):
        run_query(shop_db, sql)


def test_database_file_is_unchanged_after_attacks(shop_db):
    before = shop_db.read_bytes()
    for sql in ["DELETE FROM orders", "VACUUM", "UPDATE orders SET status = 'x'"]:
        with pytest.raises(QueryFailed):
            run_query(shop_db, sql)
    assert shop_db.read_bytes() == before


def test_time_limit_stops_endless_query(shop_db):
    endless = "WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM r) SELECT MAX(x) FROM r"
    with pytest.raises(QueryFailed, match="longer than"):
        run_query(shop_db, endless, timeout_s=0.3)


def test_row_limit_and_truncated_flag(shop_db):
    result = run_query(shop_db, "SELECT order_id FROM orders", limit=5)
    assert len(result.rows) == 5
    assert result.truncated
    small = run_query(shop_db, "SELECT id FROM products", limit=100)
    assert len(small.rows) == 40 and not small.truncated


def test_masking_hides_emails_and_phones_but_not_dates():
    assert mask_text("lucy.bennett@example.com") == "l***@example.com"
    assert mask_text("call +971 50 000 1234 now") == "call [phone hidden] now"
    assert mask_text("2025-04-01") == "2025-04-01"
    assert mask_text(42) == 42
    assert mask_personal("full_name", "Lucy Bennett") == "L*** B***"
    assert mask_personal("address", "Building 1, Street 2, Dubai") == "[address hidden]"

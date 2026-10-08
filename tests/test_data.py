"""The generated database: deterministic, consistent and free of real personal data."""

import hashlib
import sqlite3

from evals.make_gold import GOLD_PATH, QUESTIONS_PATH, compute_gold, load_jsonl
from shop_data_mcp import config
from shop_data_mcp.generate_data import generate


def query(path, sql):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql).fetchall()


def test_same_seed_same_database(shop_db, tmp_path):
    again = tmp_path / "again.db"
    generate(again)
    assert hashlib.sha256(again.read_bytes()).digest() == hashlib.sha256(shop_db.read_bytes()).digest()


def test_schema_matches_config(shop_db):
    for table, info in config.SCHEMA.items():
        columns = [row[1] for row in query(shop_db, f"PRAGMA table_info({table})")]
        assert columns == list(info["columns"]), table


def test_personal_column_names_only_exist_in_customers():
    # The guard recognises personal columns by name, so the names must be unique.
    for table, info in config.SCHEMA.items():
        if table != "customers":
            assert not config.PII_COLUMNS & set(info["columns"]), table


def test_fake_contact_details_only(shop_db):
    assert query(shop_db, "SELECT COUNT(*) FROM customers WHERE email NOT LIKE '%@example.com'") == [(0,)]
    assert query(shop_db, "SELECT COUNT(*) FROM customers WHERE phone NOT LIKE '+971 50 000 ____'") == [(0,)]
    assert query(shop_db, "SELECT COUNT(DISTINCT email) FROM customers") == [(2000,)]


def test_order_totals_match_their_lines(shop_db):
    mismatches = query(
        shop_db,
        "SELECT COUNT(*) FROM orders o JOIN (SELECT order_id, SUM(qty * unit_price_aed) AS s "
        "FROM order_items GROUP BY order_id) i ON i.order_id = o.order_id WHERE ABS(o.total_aed - i.s) > 0.01",
    )
    assert mismatches == [(0,)]


def test_stored_gold_answers_match_a_fresh_database(shop_db):
    stored = load_jsonl(GOLD_PATH)
    fresh = compute_gold(load_jsonl(QUESTIONS_PATH))  # config.DB_PATH points at the fresh test database
    assert fresh == stored
    assert len(stored) == 50

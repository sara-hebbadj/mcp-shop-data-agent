"""Layer 1: the SQL guard decides before anything touches the database."""

import pytest

from evals.make_gold import QUESTIONS_PATH, load_jsonl
from evals.run_guard import UNSAFE_PATH
from shop_data_mcp.guard import check_sql

ALLOWED = [
    "SELECT COUNT(*) FROM orders",
    "SELECT COUNT(*) FROM orders;",  # one trailing semicolon is fine
    "select status, count(*) from ORDERS group by status",
    "SELECT 'DROP TABLE orders' AS just_text",  # a string, not a command
    "SELECT 1 -- ; DROP TABLE orders",  # a comment, not a second statement
    "SELECT COUNT(DISTINCT email) FROM customers",  # personal column inside COUNT
    "SELECT id FROM customers WHERE email LIKE '%@example.com'",  # personal column only in WHERE
    "SELECT city, COUNT(*) FROM customers GROUP BY city ORDER BY COUNT(*) DESC",
    "SELECT o.* FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.city = 'Dubai'",
    "WITH m AS (SELECT strftime('%Y-%m', order_date) AS month, SUM(total_aed) AS r FROM orders GROUP BY 1) "
    "SELECT month, r FROM m ORDER BY r DESC LIMIT 1",
    "SELECT name FROM products UNION SELECT category FROM products",
    "SELECT * FROM main.products",
]

BLOCKED = [
    ("", "empty"),
    ("SELECT 1" + " " * 6000, "too_long"),
    ("SELEC * FROM orders", "parse_error"),
    ("SELECT 1; DROP TABLE orders", "multiple_statements"),
    ("DELETE FROM orders", "not_select"),
    ("WITH x AS (SELECT 1) DELETE FROM orders", "not_select"),
    ("UPDATE products SET price_aed = 0", "not_select"),
    ("PRAGMA table_info(orders)", "not_select"),
    ("ATTACH DATABASE 'x.db' AS x", "not_select"),
    ("VACUUM INTO '/tmp/copy.db'", "not_select"),
    ("SELECT load_extension('evil.so')", "denied_function"),
    ("SELECT randomblob(1000000000)", "denied_function"),
    ("SELECT name FROM sqlite_master", "unknown_table"),
    ("SELECT * FROM pragma_table_info('customers')", "table_function"),
    ("SELECT * FROM temp.orders", "other_database"),
    ("SELECT secret FROM orders", "unknown_column"),
    ("SELECT email FROM customers", "personal_data"),
    ("SELECT * FROM customers", "personal_data"),  # * expands to the personal columns
    ("SELECT c.* FROM orders o JOIN customers c ON c.id = o.customer_id", "personal_data"),
    ("SELECT upper(phone) FROM customers", "personal_data"),
    ("SELECT MAX(email) FROM customers", "personal_data"),  # only COUNT is safe
    ("SELECT group_concat(address) FROM customers", "personal_data"),
    ("WITH t AS (SELECT email AS e FROM customers) SELECT e FROM t", "personal_data"),
    ("SELECT (SELECT full_name FROM customers LIMIT 1) AS x", "personal_data"),
    ("SELECT 1 UNION SELECT email FROM customers", "personal_data"),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_safe_queries_are_allowed(sql):
    verdict = check_sql(sql)
    assert verdict.allowed, verdict.message


@pytest.mark.parametrize(("sql", "rule"), BLOCKED)
def test_unsafe_queries_are_blocked_with_the_right_rule(sql, rule):
    verdict = check_sql(sql)
    assert not verdict.allowed
    assert verdict.rule == rule


def test_every_gold_query_passes_the_guard():
    for question in load_jsonl(QUESTIONS_PATH):
        assert check_sql(question["gold_sql"]).allowed, question["id"]


def test_unsafe_set_is_blocked_except_resource_abuse():
    # Endless or huge queries look like normal SELECTs; the time limit (layer 2) stops them.
    for case in load_jsonl(UNSAFE_PATH):
        for sql in case["attack_sql"]:
            if case["category"] != "resource_abuse":
                assert not check_sql(sql).allowed, sql

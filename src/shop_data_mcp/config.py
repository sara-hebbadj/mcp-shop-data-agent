"""Settings and the shop schema in one place.

The schema dictionary below is the single description of the database that the
guard, the MCP server and the agent prompt all use. A test checks that it matches
the real `data/shop.db`, so it cannot silently drift.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Paths can be overridden with environment variables (useful for Claude Desktop config).
DB_PATH = Path(os.environ.get("SHOP_DB_PATH", REPO_ROOT / "data" / "shop.db"))
QUERY_LOG_PATH = Path(os.environ.get("SHOP_QUERY_LOG", REPO_ROOT / "logs" / "query_log.jsonl"))

# Limits enforced in code (not just asked for in a prompt).
DEFAULT_ROW_LIMIT = 200
MAX_ROW_LIMIT = 1000
QUERY_TIMEOUT_SECONDS = 3.0
MAX_SQL_CHARS = 5000

# Personal data. Each of these column names exists in exactly one table
# (customers), so the guard can recognise them by name alone. A test enforces
# that no other table reuses these names. This is why the customer name column
# is called `full_name` and not `name` (products also has a `name` column).
PII_COLUMNS = {"full_name", "email", "phone", "address"}

# The only aggregate that may touch a personal column: COUNT(email) or
# COUNT(DISTINCT email) reveals how many, never who.
PII_SAFE_AGGREGATES = {"count"}

# Functions we never allow, even though a stock Python sqlite3 build does not
# ship most of them. Defence in depth.
DENIED_FUNCTIONS = {
    "load_extension",
    "readfile",
    "writefile",
    "edit",
    "fts3_tokenizer",
    "sqlite_compileoption_get",
    "sqlite_compileoption_used",
    "randomblob",  # can allocate huge blobs (memory denial of service)
    "zeroblob",
}

# table -> description and columns (name -> (sqlite type, description)).
SCHEMA: dict[str, dict] = {
    "products": {
        "description": "The 40 Lumi Skin products (fictional skincare shop).",
        "columns": {
            "id": ("TEXT", "Product id, e.g. P001"),
            "name": ("TEXT", "Product name"),
            "category": ("TEXT", "cleanser, serum, moisturiser, sunscreen or mask"),
            "skin_type": ("TEXT", "all, dry, oily, combination or sensitive"),
            "key_ingredients": ("TEXT", "Main ingredients, separated by ';'"),
            "price_aed": ("REAL", "Current list price in AED"),
            "stock": ("INTEGER", "Units in stock today (0 = out of stock)"),
        },
    },
    "customers": {
        "description": "Registered customers. full_name, email, phone and address are personal data.",
        "columns": {
            "id": ("TEXT", "Customer id, e.g. C0001"),
            "full_name": ("TEXT", "PERSONAL: customer name"),
            "email": ("TEXT", "PERSONAL: email (example.com)"),
            "phone": ("TEXT", "PERSONAL: phone (+971 50 000 xxxx)"),
            "address": ("TEXT", "PERSONAL: street address"),
            "city": ("TEXT", "Home city, e.g. Dubai, Riyadh"),
            "country": ("TEXT", "Home country, e.g. United Arab Emirates"),
            "language": ("TEXT", "Preferred language: ar, en or fr"),
            "signup_date": ("TEXT", "Account creation date, YYYY-MM-DD"),
        },
    },
    "orders": {
        "description": "One row per order (April 2025 to September 2026).",
        "columns": {
            "order_id": ("TEXT", "Order id, e.g. LS-10001"),
            "customer_id": ("TEXT", "-> customers.id"),
            "order_date": ("TEXT", "Date the order was placed, YYYY-MM-DD"),
            "status": ("TEXT", "processing, shipped, delivered, returned (every item returned) or cancelled"),
            "channel": ("TEXT", "website, app or instagram"),
            "payment_method": ("TEXT", "card or cod (cash on delivery, only up to AED 1,000)"),
            "total_aed": ("REAL", "Order value in AED = sum of its order_items lines (delivery is free)"),
            "destination_city": ("TEXT", "Delivery city"),
            "destination_country": ("TEXT", "Delivery country"),
            "promised_date": ("TEXT", "Promised delivery date (UAE: order + 3 days, other GCC: + 7 days)"),
            "shipped_date": ("TEXT", "Date handed to the courier (NULL if not shipped)"),
            "delivered_date": ("TEXT", "Date delivered (NULL if not delivered)"),
        },
    },
    "order_items": {
        "description": "Order lines: which products were in each order.",
        "columns": {
            "order_id": ("TEXT", "-> orders.order_id"),
            "product_id": ("TEXT", "-> products.id"),
            "qty": ("INTEGER", "Units ordered"),
            "unit_price_aed": ("REAL", "Price paid per unit in AED"),
        },
    },
    "returns": {
        "description": "Returned order lines (a partial return keeps the order status 'delivered').",
        "columns": {
            "return_id": ("TEXT", "Return id, e.g. R0001"),
            "order_id": ("TEXT", "-> orders.order_id"),
            "product_id": ("TEXT", "-> products.id"),
            "qty": ("INTEGER", "Units returned"),
            "return_date": ("TEXT", "Date the return was received, YYYY-MM-DD"),
            "reason": ("TEXT", "damaged, skin_reaction, wrong_item, not_as_described or changed_mind"),
            "refund_aed": ("REAL", "Amount refunded in AED"),
        },
    },
    "reviews": {
        "description": "Product reviews left after delivery.",
        "columns": {
            "review_id": ("TEXT", "Review id, e.g. V0001"),
            "order_id": ("TEXT", "-> orders.order_id"),
            "product_id": ("TEXT", "-> products.id"),
            "customer_id": ("TEXT", "-> customers.id"),
            "rating": ("INTEGER", "1 to 5 stars"),
            "review_date": ("TEXT", "YYYY-MM-DD"),
            "comment": ("TEXT", "Short review text (en, ar or fr)"),
        },
    },
}

# Business definitions shared with every client through the schema resource.
# Writing them down is what makes "revenue" mean the same thing for every model.
BUSINESS_RULES = [
    "Revenue = SUM(order_items.qty * order_items.unit_price_aed) (equivalently SUM(orders.total_aed)) "
    "over orders whose status is not 'cancelled'. Refunds are NOT subtracted unless the question says 'net'.",
    "Net revenue = revenue minus SUM(returns.refund_aed).",
    "A month is strftime('%Y-%m', order_date). Dates are ISO text, so compare them as strings.",
    "Units sold = SUM(order_items.qty) over orders that are not cancelled.",
    "Product return rate = units returned (returns.qty) / units sold for that product.",
    "Average order value (AOV) = revenue / number of non-cancelled orders.",
    "A repeat customer has 2 or more non-cancelled orders. "
    "Repeat rate = repeat customers / customers with at least 1 non-cancelled order.",
    "Delivery delay in days = julianday(delivered_date) - julianday(promised_date), "
    "only for orders with a delivered_date. Positive = late. Late means delay > 0.",
    "Personal columns (full_name, email, phone, address) can only be used inside COUNT().",
]

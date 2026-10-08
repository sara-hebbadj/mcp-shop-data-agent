"""Write evals/questions.jsonl: 30 English questions + 20 Arabic translations.

The Arabic questions are translations of 20 of the English ones and share their
gold SQL ("pair_id"). That makes the language comparison fair: same SQL, same
answer, only the language of the question changes.

Run:  python -m evals.build_questions   (then python -m evals.make_gold)
"""

import json
from pathlib import Path

NOT_CANCELLED = "status != 'cancelled'"

# (id, difficulty, ordered, question, gold_sql)
ENGLISH = [
    ("en01", "easy", False, "How many customers are registered?",
     "SELECT COUNT(*) AS customers FROM customers"),
    ("en02", "easy", False, "How many orders were cancelled?",
     "SELECT COUNT(*) AS cancelled_orders FROM orders WHERE status = 'cancelled'"),
    ("en03", "easy", False, "What is the most expensive product, and what is its price?",
     "SELECT name, price_aed FROM products ORDER BY price_aed DESC LIMIT 1"),
    ("en04", "easy", False, "How many products are out of stock right now?",
     "SELECT COUNT(*) AS out_of_stock FROM products WHERE stock = 0"),
    ("en05", "easy", False, "How many orders were paid with cash on delivery?",
     "SELECT COUNT(*) AS cod_orders FROM orders WHERE payment_method = 'cod'"),
    ("en06", "easy", False, "What is the average review rating across all reviews? Round to 2 decimals.",
     "SELECT ROUND(AVG(rating), 2) AS avg_rating FROM reviews"),
    ("en07", "easy", False, "How many products are there in each category?",
     "SELECT category, COUNT(*) AS products FROM products GROUP BY category"),
    ("en08", "easy", False, "How many customers prefer Arabic as their language?",
     "SELECT COUNT(*) AS arabic_customers FROM customers WHERE language = 'ar'"),
    ("en09", "easy", False, "How many orders came through Instagram?",
     "SELECT COUNT(*) AS instagram_orders FROM orders WHERE channel = 'instagram'"),
    ("en10", "easy", False, "What is the total amount refunded for returns, in AED?",
     "SELECT ROUND(SUM(refund_aed), 2) AS total_refunded_aed FROM returns"),
    ("en11", "medium", False, "What was the total revenue in 2025?",
     f"SELECT ROUND(SUM(total_aed), 2) AS revenue_aed FROM orders "
     f"WHERE {NOT_CANCELLED} AND order_date BETWEEN '2025-01-01' AND '2025-12-31'"),
    ("en12", "medium", True, "Show the revenue for each month of 2026, in month order.",
     f"SELECT strftime('%Y-%m', order_date) AS month, ROUND(SUM(total_aed), 2) AS revenue_aed FROM orders "
     f"WHERE {NOT_CANCELLED} AND order_date >= '2026-01-01' GROUP BY month ORDER BY month"),
    ("en13", "medium", False, "Which month had the highest revenue?",
     f"SELECT strftime('%Y-%m', order_date) AS month, ROUND(SUM(total_aed), 2) AS revenue_aed FROM orders "
     f"WHERE {NOT_CANCELLED} GROUP BY month ORDER BY revenue_aed DESC LIMIT 1"),
    ("en14", "medium", True, "What are the top 5 products by units sold?",
     f"SELECT p.name, SUM(oi.qty) AS units FROM order_items oi JOIN orders o ON o.order_id = oi.order_id "
     f"JOIN products p ON p.id = oi.product_id WHERE o.{NOT_CANCELLED} "
     f"GROUP BY p.id, p.name ORDER BY units DESC LIMIT 5"),
    ("en15", "medium", False, "What is the average delivery delay in days for each destination city?",
     "SELECT destination_city, ROUND(AVG(julianday(delivered_date) - julianday(promised_date)), 2) AS avg_delay_days "
     "FROM orders WHERE delivered_date IS NOT NULL GROUP BY destination_city"),
    ("en16", "medium", False, "Which destination city received the most orders?",
     "SELECT destination_city, COUNT(*) AS orders FROM orders GROUP BY destination_city "
     "ORDER BY orders DESC LIMIT 1"),
    ("en17", "medium", False, "What is the revenue for each sales channel?",
     f"SELECT channel, ROUND(SUM(total_aed), 2) AS revenue_aed FROM orders WHERE {NOT_CANCELLED} GROUP BY channel"),
    ("en18", "medium", False, "What is the average order value? Round to 2 decimals.",
     f"SELECT ROUND(SUM(total_aed) / COUNT(*), 2) AS aov_aed FROM orders WHERE {NOT_CANCELLED}"),
    ("en19", "medium", False, "How many orders were delivered later than the promised date?",
     "SELECT COUNT(*) AS late_orders FROM orders "
     "WHERE delivered_date IS NOT NULL AND julianday(delivered_date) - julianday(promised_date) > 0"),
    ("en20", "medium", False, "How many returned lines are there for each return reason?",
     "SELECT reason, COUNT(*) AS returns FROM returns GROUP BY reason"),
    ("en21", "medium", False, "What is the revenue for each product category?",
     f"SELECT p.category, ROUND(SUM(oi.qty * oi.unit_price_aed), 2) AS revenue_aed FROM order_items oi "
     f"JOIN orders o ON o.order_id = oi.order_id JOIN products p ON p.id = oi.product_id "
     f"WHERE o.{NOT_CANCELLED} GROUP BY p.category"),
    ("en22", "medium", False, "What is the average review rating for each product category? Round to 2 decimals.",
     "SELECT p.category, ROUND(AVG(r.rating), 2) AS avg_rating FROM reviews r "
     "JOIN products p ON p.id = r.product_id GROUP BY p.category"),
    ("en23", "medium", False, "How many orders were shipped to each destination country?",
     "SELECT destination_country, COUNT(*) AS orders FROM orders GROUP BY destination_country"),
    ("en24", "medium", False, "What percentage of all orders were cancelled? Round to 1 decimal.",
     "SELECT ROUND(100.0 * SUM(status = 'cancelled') / COUNT(*), 1) AS cancelled_pct FROM orders"),
    ("en25", "hard", False, "What is the repeat customer rate (as a percentage, 1 decimal)?",
     f"SELECT ROUND(100.0 * SUM(n >= 2) / COUNT(*), 1) AS repeat_rate_pct FROM "
     f"(SELECT customer_id, COUNT(*) AS n FROM orders WHERE {NOT_CANCELLED} GROUP BY customer_id)"),
    ("en26", "hard", True, "Which 3 products have the highest return rate (units returned divided by units sold)?",
     f"SELECT p.name, ROUND(1.0 * COALESCE(r.units, 0) / s.units, 4) AS return_rate FROM products p "
     f"JOIN (SELECT oi.product_id, SUM(oi.qty) AS units FROM order_items oi JOIN orders o "
     f"ON o.order_id = oi.order_id WHERE o.{NOT_CANCELLED} GROUP BY oi.product_id) s ON s.product_id = p.id "
     f"LEFT JOIN (SELECT product_id, SUM(qty) AS units FROM returns GROUP BY product_id) r ON r.product_id = p.id "
     f"ORDER BY return_rate DESC LIMIT 3"),
    ("en27", "hard", False, "Among products with at least 20 reviews, which one has the lowest average rating?",
     "SELECT p.name, ROUND(AVG(r.rating), 2) AS avg_rating FROM reviews r JOIN products p ON p.id = r.product_id "
     "GROUP BY p.id, p.name HAVING COUNT(*) >= 20 ORDER BY avg_rating ASC LIMIT 1"),
    ("en28", "hard", False, "Which month had the biggest increase in revenue compared with the previous month?",
     f"SELECT month, ROUND(revenue - LAG(revenue) OVER (ORDER BY month), 2) AS increase_aed FROM "
     f"(SELECT strftime('%Y-%m', order_date) AS month, SUM(total_aed) AS revenue FROM orders "
     f"WHERE {NOT_CANCELLED} GROUP BY month) ORDER BY increase_aed DESC LIMIT 1"),
    ("en29", "hard", False,
     "What was the net revenue (revenue minus refunds on those same orders) for orders placed "
     "from July to September 2026?",
     f"SELECT ROUND((SELECT SUM(total_aed) FROM orders WHERE {NOT_CANCELLED} "
     f"AND order_date BETWEEN '2026-07-01' AND '2026-09-30') - "
     f"(SELECT COALESCE(SUM(r.refund_aed), 0) FROM returns r JOIN orders o ON o.order_id = r.order_id "
     f"WHERE o.order_date BETWEEN '2026-07-01' AND '2026-09-30'), 2) AS net_revenue_aed"),
    ("en30", "hard", False, "How many customers placed their very first order (of any status) in 2026?",
     "SELECT COUNT(*) AS new_customers_2026 FROM "
     "(SELECT customer_id, MIN(order_date) AS first_order FROM orders GROUP BY customer_id) "
     "WHERE first_order >= '2026-01-01'"),
]

# Arabic translations: (id, english pair id, question)
ARABIC = [
    ("ar01", "en01", "كم عدد العملاء المسجلين؟"),
    ("ar02", "en02", "كم عدد الطلبات التي تم إلغاؤها؟"),
    ("ar03", "en03", "ما هو أغلى منتج، وما سعره؟"),
    ("ar05", "en05", "كم عدد الطلبات التي دُفعت نقداً عند الاستلام؟"),
    ("ar07", "en07", "كم عدد المنتجات في كل فئة؟"),
    ("ar11", "en11", "ما إجمالي الإيرادات في عام 2025؟"),
    ("ar12", "en12", "اعرض الإيرادات لكل شهر من عام 2026 مرتبة حسب الشهر."),
    ("ar13", "en13", "ما الشهر الذي حقق أعلى إيرادات؟"),
    ("ar14", "en14", "ما هي أفضل 5 منتجات من حيث عدد الوحدات المباعة؟"),
    ("ar15", "en15", "ما متوسط التأخير في التوصيل بالأيام لكل مدينة توصيل؟"),
    ("ar17", "en17", "ما الإيرادات لكل قناة بيع؟"),
    ("ar18", "en18", "ما متوسط قيمة الطلب؟ قرّب الناتج إلى منزلتين عشريتين."),
    ("ar19", "en19", "كم عدد الطلبات التي وصلت بعد موعد التسليم المحدد؟"),
    ("ar20", "en20", "كم عدد بنود الإرجاع لكل سبب من أسباب الإرجاع؟"),
    ("ar21", "en21", "ما الإيرادات لكل فئة من فئات المنتجات؟"),
    ("ar25", "en25", "ما نسبة العملاء المتكررين (كنسبة مئوية بمنزلة عشرية واحدة)؟"),
    ("ar26", "en26", "ما هي المنتجات الثلاثة الأعلى في معدل الإرجاع (الوحدات المرتجعة مقسومة على الوحدات المباعة)؟"),
    ("ar27", "en27", "من بين المنتجات التي لديها 20 تقييماً على الأقل، أيها لديه أدنى متوسط تقييم؟"),
    ("ar28", "en28", "ما الشهر الذي شهد أكبر زيادة في الإيرادات مقارنة بالشهر السابق؟"),
    ("ar30", "en30", "كم عدد العملاء الذين قدموا أول طلب لهم على الإطلاق (بأي حالة) في عام 2026؟"),
]


def build() -> list[dict]:
    rows = []
    by_id = {}
    for qid, difficulty, ordered, question, sql in ENGLISH:
        row = {"id": qid, "lang": "en", "difficulty": difficulty, "ordered": ordered,
               "question": question, "gold_sql": sql, "pair_id": None}
        rows.append(row)
        by_id[qid] = row
    for qid, pair, question in ARABIC:
        english = by_id[pair]
        rows.append({"id": qid, "lang": "ar", "difficulty": english["difficulty"], "ordered": english["ordered"],
                     "question": question, "gold_sql": english["gold_sql"], "pair_id": pair})
    return rows


if __name__ == "__main__":
    out = Path(__file__).with_name("questions.jsonl")
    questions = build()
    with out.open("w", encoding="utf-8") as file:
        for row in questions:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Wrote {len(questions)} questions to {out}")

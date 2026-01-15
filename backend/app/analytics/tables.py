"""Analytics scope table helpers."""

from __future__ import annotations

from app.core.config import Settings
from app.db.clickhouse import fact_table, issues_table

SCOPE_VALUES = {"clean", "issues", "all"}

ANALYTICS_COLUMNS = [
    "tenant_id",
    "owner_user_id",
    "transaction_id",
    "customer_id",
    "customer_name",
    "email",
    "phone",
    "country",
    "city",
    "postal_code",
    "department",
    "category",
    "product_name",
    "product_code",
    "product_id",
    "quantity",
    "unit_price",
    "discount_percent",
    "tax_rate",
    "payment_method",
    "status",
    "tier",
    "order_date",
    "is_returning_customer",
    "loyalty_points",
    "rating",
    "region_code",
    "sales_rep_id",
    "total_amount",
    "user_id",
    "amount",
    "event_ts",
]


def _issues_scope_table(settings: Settings) -> str:
    """Build a derived table that maps issue rows into the analytics schema."""
    table = issues_table(settings)
    return f"""
        (
            WITH
                nullIf(raw_columns['customer_id'], '') AS customer_id_raw,
                nullIf(raw_columns['user_id'], '') AS user_id_raw,
                coalesce(customer_id_raw, user_id_raw) AS customer_id,
                coalesce(user_id_raw, customer_id_raw) AS user_id,
                nullIf(raw_columns['product_id'], '') AS product_id_raw,
                nullIf(raw_columns['product_code'], '') AS product_code_raw,
                coalesce(product_id_raw, product_code_raw) AS product_id,
                coalesce(product_code_raw, product_id_raw) AS product_code,
                toFloat64OrNull(nullIf(raw_columns['quantity'], '')) AS quantity,
                toFloat64OrNull(nullIf(raw_columns['unit_price'], '')) AS unit_price_raw,
                toFloat64OrNull(nullIf(raw_columns['price'], '')) AS price_raw,
                coalesce(unit_price_raw, price_raw) AS unit_price,
                toFloat64OrNull(nullIf(raw_columns['discount_percent'], '')) AS discount_percent,
                toFloat64OrNull(nullIf(raw_columns['tax_rate'], '')) AS tax_rate,
                toFloat64OrNull(nullIf(raw_columns['amount'], '')) AS amount_raw,
                toFloat64OrNull(nullIf(raw_columns['total_amount'], '')) AS total_amount_raw,
                coalesce(amount_raw, total_amount_raw, quantity * unit_price) AS amount,
                coalesce(total_amount_raw, amount_raw, quantity * unit_price) AS total_amount,
                parseDateTimeBestEffortOrNull(nullIf(raw_columns['order_date'], '')) AS order_date_raw,
                parseDateTimeBestEffortOrNull(nullIf(raw_columns['event_ts'], '')) AS event_ts_raw,
                coalesce(order_date_raw, event_ts_raw) AS order_date,
                coalesce(event_ts_raw, order_date_raw) AS event_ts,
                toUInt8OrNull(nullIf(raw_columns['is_returning_customer'], '')) AS is_returning_customer
            SELECT
                tenant_id,
                NULL AS owner_user_id,
                transaction_id,
                customer_id,
                nullIf(raw_columns['customer_name'], '') AS customer_name,
                nullIf(raw_columns['email'], '') AS email,
                nullIf(raw_columns['phone'], '') AS phone,
                nullIf(raw_columns['country'], '') AS country,
                nullIf(raw_columns['city'], '') AS city,
                nullIf(raw_columns['postal_code'], '') AS postal_code,
                nullIf(raw_columns['department'], '') AS department,
                nullIf(raw_columns['category'], '') AS category,
                nullIf(raw_columns['product_name'], '') AS product_name,
                product_code,
                product_id,
                quantity,
                unit_price,
                discount_percent,
                tax_rate,
                nullIf(raw_columns['payment_method'], '') AS payment_method,
                nullIf(raw_columns['status'], '') AS status,
                nullIf(raw_columns['tier'], '') AS tier,
                order_date,
                is_returning_customer,
                toFloat64OrNull(nullIf(raw_columns['loyalty_points'], '')) AS loyalty_points,
                toFloat64OrNull(nullIf(raw_columns['rating'], '')) AS rating,
                nullIf(raw_columns['region_code'], '') AS region_code,
                nullIf(raw_columns['sales_rep_id'], '') AS sales_rep_id,
                total_amount,
                user_id,
                amount,
                event_ts
            FROM {table}
        ) AS issues_scope
    """


def build_scope_table(settings: Settings, scope: str) -> str:
    """Resolve the ClickHouse table or derived scope for analytics queries."""
    clean_table = fact_table(settings)
    if scope == "clean":
        return clean_table
    issues_scope = _issues_scope_table(settings)
    if scope == "issues":
        return issues_scope
    columns = ", ".join(ANALYTICS_COLUMNS)
    return f"(SELECT {columns} FROM {clean_table} UNION ALL SELECT {columns} FROM {issues_scope}) AS all_scope"

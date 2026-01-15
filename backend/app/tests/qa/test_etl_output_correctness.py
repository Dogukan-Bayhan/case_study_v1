"""ETL output correctness checks against ClickHouse."""

from __future__ import annotations

import os

import pytest

from .utils import fetch_column_types, fetch_missing_ratios


REQUIRED_TRANSACTION_COLUMNS = [
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
]

MAX_NULL_RATIO = float(os.getenv("QA_REQUIRED_MAX_NULL_RATIO", "0.95"))


def test_etl_required_columns_present_in_clickhouse(clickhouse_client, clickhouse_database):
    """
    Validate that all required transaction columns exist in ClickHouse after ETL.

    Why: ETL schema drift can silently drop columns, breaking downstream analytics and APIs.
    Failure caught: missing or renamed columns in `fact_transactions_clean`.
    Expected: every column listed in REQUIRED_TRANSACTION_COLUMNS exists in the ClickHouse table.
    """
    rows = clickhouse_client.execute(
        """
        SELECT name
        FROM system.columns
        WHERE database = %(db)s AND table = 'fact_transactions_clean'
        """,
        {"db": clickhouse_database},
    )
    existing = {name for (name,) in rows}
    missing = [col for col in REQUIRED_TRANSACTION_COLUMNS if col not in existing]
    assert not missing, f"Missing required columns in ClickHouse: {missing}"


def test_etl_required_columns_not_null_heavy(clickhouse_client, clickhouse_fact_table, clickhouse_database):
    """
    Ensure required columns are populated and not effectively NULL after transformation.

    Why: A successful ETL run can still be incorrect if required fields are mostly empty.
    Failure caught: mapping errors or upstream data issues that leave required fields null-heavy.
    Expected: each required column has a null/blank ratio below the QA_REQUIRED_MAX_NULL_RATIO threshold.
    """
    column_types = fetch_column_types(
        clickhouse_client,
        clickhouse_database,
        "fact_transactions_clean",
        REQUIRED_TRANSACTION_COLUMNS,
    )
    total_rows, ratios = fetch_missing_ratios(clickhouse_client, clickhouse_fact_table, column_types)
    assert total_rows > 0, "ClickHouse fact table is empty; ETL output is missing."

    offenders = {
        col: stats["ratio"]
        for col, stats in ratios.items()
        if stats["ratio"] >= MAX_NULL_RATIO
    }
    assert not offenders, (
        "Required columns exceed null ratio threshold "
        f"{MAX_NULL_RATIO:.2f}: {offenders}"
    )

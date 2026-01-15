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
    """Validate required transaction columns exist in ClickHouse after ETL.

    Business purpose:
        Ensure ETL output schema matches expected analytics requirements.
    Why it exists:
        Detects schema drift that could break downstream APIs.
    Where used:
        QA test suite for ETL output correctness.
    Inputs:
        clickhouse_client: ClickHouse client for metadata queries.
        clickhouse_database: ClickHouse database name.
    Returns:
        None; asserts required columns exist.
    """
    # Query system.columns to validate the clean fact table schema.
    # Metadata-only query avoids scanning table data.
    # Ensures schema drift is caught quickly in QA.
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
    """Ensure required columns are not null-heavy after transformation.

    Business purpose:
        Validate ETL output has sufficient data completeness.
    Why it exists:
        Prevents silently broken mappings that leave required columns empty.
    Where used:
        QA test suite for ETL output correctness.
    Inputs:
        clickhouse_client: ClickHouse client for validation queries.
        clickhouse_fact_table: Clean fact table name.
        clickhouse_database: ClickHouse database name.
    Returns:
        None; asserts null ratios are below threshold.
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

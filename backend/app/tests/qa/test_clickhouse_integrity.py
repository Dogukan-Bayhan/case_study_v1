"""ClickHouse data integrity checks for analytics readiness."""

from __future__ import annotations

import os

from .utils import fetch_column_types, fetch_missing_ratios


CRITICAL_COLUMNS = [
    "tenant_id",
    "owner_user_id",
    "transaction_id",
    "order_date",
    "total_amount",
    "amount",
    "event_ts",
]

STRICT_NON_NULL_COLUMNS = {"tenant_id", "owner_user_id", "transaction_id"}
CRITICAL_MAX_NULL_RATIO = float(os.getenv("QA_CRITICAL_MAX_NULL_RATIO", "0.05"))


def test_clickhouse_fact_table_has_rows(clickhouse_client, clickhouse_fact_table):
    """Confirm ClickHouse contains transaction data after ETL.

    Business purpose:
        Ensure analytics tables are populated for the UI and APIs.
    Why it exists:
        Detects empty loads or accidental truncation.
    Where used:
        QA test suite for ClickHouse integrity.
    Inputs:
        clickhouse_client: ClickHouse client for queries.
        clickhouse_fact_table: Clean fact table name.
    Returns:
        None; asserts table has rows.
    """
    # Query counts rows in the clean fact table.
    # COUNT(*) is a lightweight aggregate and returns a single scalar.
    # Used to fail fast if the table is empty after ETL.
    total_rows = clickhouse_client.execute(f"SELECT count() FROM {clickhouse_fact_table}")[0][0]
    assert total_rows > 0, "fact_transactions_clean is empty."


def test_clickhouse_null_ratios_for_critical_columns(
    clickhouse_client,
    clickhouse_fact_table,
    clickhouse_database,
):
    """Validate null/blank ratios for critical analytics columns in ClickHouse.

    Business purpose:
        Ensure critical columns are populated for analytics readiness.
    Why it exists:
        Detects parsing or mapping failures that leave critical fields empty.
    Where used:
        QA test suite for ClickHouse integrity.
    Inputs:
        clickhouse_client: ClickHouse client for queries.
        clickhouse_fact_table: Clean fact table name.
        clickhouse_database: ClickHouse database name.
    Returns:
        None; asserts null ratios are within thresholds.
    """
    column_types = fetch_column_types(
        clickhouse_client,
        clickhouse_database,
        "fact_transactions_clean",
        CRITICAL_COLUMNS,
    )
    total_rows, ratios = fetch_missing_ratios(clickhouse_client, clickhouse_fact_table, column_types)
    assert total_rows > 0, "fact_transactions_clean is empty; cannot evaluate NULL ratios."

    strict_failures = {col: ratios[col]["missing"] for col in STRICT_NON_NULL_COLUMNS if ratios[col]["missing"]}
    assert not strict_failures, f"Strict columns contain NULL/blank values: {strict_failures}"

    ratio_failures = {
        col: stats["ratio"]
        for col, stats in ratios.items()
        if col not in STRICT_NON_NULL_COLUMNS and stats["ratio"] > CRITICAL_MAX_NULL_RATIO
    }
    assert not ratio_failures, (
        "Critical columns exceed null ratio threshold "
        f"{CRITICAL_MAX_NULL_RATIO:.2f}: {ratio_failures}"
    )

    # Query counts rows that are analytics-ready based on key fields.
    # Aggregate check avoids fetching row-level data in tests.
    # WHERE clause restricts scan to rows with required fields populated.
    ready_rows = clickhouse_client.execute(
        f"""
        SELECT count()
        FROM {clickhouse_fact_table}
        WHERE order_date IS NOT NULL AND total_amount IS NOT NULL
        """
    )[0][0]
    assert ready_rows > 0, "No analytics-ready rows found with order_date and total_amount populated."

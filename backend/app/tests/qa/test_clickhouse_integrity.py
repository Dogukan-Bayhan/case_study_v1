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
    """
    Confirm that ClickHouse contains transaction data after ETL.

    Why: An empty fact table makes analytics endpoints and UI unusable.
    Failure caught: ETL not executed, load failed, or accidental table truncation.
    Expected: fact_transactions_clean has at least one row.
    """
    total_rows = clickhouse_client.execute(f"SELECT count() FROM {clickhouse_fact_table}")[0][0]
    assert total_rows > 0, "fact_transactions_clean is empty."


def test_clickhouse_null_ratios_for_critical_columns(
    clickhouse_client,
    clickhouse_fact_table,
    clickhouse_database,
):
    """
    Validate null/blank ratios for critical analytics columns in ClickHouse.

    Why: Core metrics depend on dates and amounts; high NULL ratios indicate unusable data.
    Failure caught: columns populated with NULLs/blanks due to failed parsing or mapping.
    Expected: strict columns are fully populated; critical columns stay below QA_CRITICAL_MAX_NULL_RATIO.
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

    ready_rows = clickhouse_client.execute(
        f"""
        SELECT count()
        FROM {clickhouse_fact_table}
        WHERE order_date IS NOT NULL AND total_amount IS NOT NULL
        """
    )[0][0]
    assert ready_rows > 0, "No analytics-ready rows found with order_date and total_amount populated."

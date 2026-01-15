"""Helpers for ClickHouse QA queries."""

from __future__ import annotations

from typing import Iterable


def fetch_column_types(
    client,
    database: str,
    table: str,
    columns: Iterable[str],
) -> list[tuple[str, str]]:
    """Return column types for requested columns, preserving order.

    Business purpose:
        Validate ClickHouse schema for QA checks.
    Why it exists:
        QA tests need column types to build type-aware checks.
    Where used:
        QA test suites that inspect ClickHouse schemas.
    Inputs:
        client: ClickHouse client for metadata queries.
        database: ClickHouse database name.
        table: ClickHouse table name.
        columns: Column names to inspect.
    Returns:
        List of (name, type) tuples in requested order.
    """
    requested = list(columns)
    # Query system.columns to retrieve column types for the table.
    # Metadata lookup avoids scanning data parts and is fast.
    # IN clause keeps the result set limited to requested columns.
    rows = client.execute(
        """
        SELECT name, type
        FROM system.columns
        WHERE database = %(db)s AND table = %(table)s AND name IN %(columns)s
        """,
        {"db": database, "table": table, "columns": tuple(requested)},
    )
    types = {name: dtype for name, dtype in rows}
    return [(name, types.get(name)) for name in requested]


def _missing_expr(column: str, dtype: str) -> str:
    """Build a type-aware missing-count expression for ClickHouse.

    Business purpose:
        Generate SQL expressions that count missing values correctly.
    Why it exists:
        String columns treat empty strings as missing; numeric columns do not.
    Where used:
        fetch_missing_ratios when assembling aggregate queries.
    Inputs:
        column: Column name to evaluate.
        dtype: ClickHouse type string.
    Returns:
        SQL expression string for counting missing values.
    """
    if "String" in dtype:
        return f"countIf({column} IS NULL OR {column} = '') AS {column}_missing"
    return f"countIf({column} IS NULL) AS {column}_missing"


def fetch_missing_ratios(
    client,
    table: str,
    column_types: list[tuple[str, str]],
) -> tuple[int, dict[str, dict[str, float]]]:
    """Return total rows plus missing counts/ratios for each column.

    Business purpose:
        Compute missingness ratios for QA validation of ClickHouse data.
    Why it exists:
        QA checks need consistent missing metrics across columns.
    Where used:
        QA tests for ETL output correctness.
    Inputs:
        client: ClickHouse client for query execution.
        table: ClickHouse table name.
        column_types: List of (column, type) tuples.
    Returns:
        Tuple of total row count and dict of missing metrics per column.
    """
    expressions = []
    columns = []
    for name, dtype in column_types:
        if dtype is None:
            raise ValueError(f"Column {name} is missing from ClickHouse.")
        expressions.append(_missing_expr(name, dtype))
        columns.append(name)

    # Query computes total rows and missing counts per column in one scan.
    # Single aggregate query avoids per-column scans in QA checks.
    # COUNT + countIf expressions keep the output size small.
    query = f"SELECT count() AS total, {', '.join(expressions)} FROM {table}"
    row = client.execute(query)[0]
    total = int(row[0])
    ratios: dict[str, dict[str, float]] = {}
    for idx, col in enumerate(columns, start=1):
        missing = int(row[idx])
        ratio = (missing / total) if total else 0.0
        ratios[col] = {"missing": missing, "ratio": ratio}
    return total, ratios

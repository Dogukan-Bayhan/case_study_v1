"""Helpers for ClickHouse QA queries."""

from __future__ import annotations

from typing import Iterable


def fetch_column_types(
    client,
    database: str,
    table: str,
    columns: Iterable[str],
) -> list[tuple[str, str]]:
    """Return column types for the requested columns, preserving order."""
    requested = list(columns)
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
    """Build a type-aware missing-count expression for ClickHouse."""
    if "String" in dtype:
        return f"countIf({column} IS NULL OR {column} = '') AS {column}_missing"
    return f"countIf({column} IS NULL) AS {column}_missing"


def fetch_missing_ratios(
    client,
    table: str,
    column_types: list[tuple[str, str]],
) -> tuple[int, dict[str, dict[str, float]]]:
    """Return total rows plus missing counts/ratios for each column."""
    expressions = []
    columns = []
    for name, dtype in column_types:
        if dtype is None:
            raise ValueError(f"Column {name} is missing from ClickHouse.")
        expressions.append(_missing_expr(name, dtype))
        columns.append(name)

    query = f"SELECT count() AS total, {', '.join(expressions)} FROM {table}"
    row = client.execute(query)[0]
    total = int(row[0])
    ratios: dict[str, dict[str, float]] = {}
    for idx, col in enumerate(columns, start=1):
        missing = int(row[idx])
        ratio = (missing / total) if total else 0.0
        ratios[col] = {"missing": missing, "ratio": ratio}
    return total, ratios

"""Analytics scope table helpers."""

from __future__ import annotations

from app.core.config import Settings
from app.db.clickhouse import all_fact_table, fact_table, issue_fact_table

SCOPE_VALUES = {"clean", "issues", "all"}

def _issues_scope_table(settings: Settings) -> str:
    """Resolve the fact table for ISSUE scope analytics.

    Business purpose:
        Isolate transactions flagged as data quality issues.
    Why it exists:
        Keeps scope mapping logic centralized for analytics queries.
    Where used:
        build_scope_table when scope is set to "issues".
    Inputs:
        settings: Runtime configuration with ClickHouse database name.
    Returns:
        Fully qualified ClickHouse table name for issue facts.
    """
    return issue_fact_table(settings)


def build_scope_table(settings: Settings, scope: str) -> str:
    """Resolve the ClickHouse table to query based on analytics scope.

    Business purpose:
        Route analytics queries to clean, issue, or combined fact tables.
    Why it exists:
        Enforces consistent scope semantics across all analytics endpoints.
    Where used:
        Analytics routers and services when selecting source tables.
    Inputs:
        settings: Runtime configuration with ClickHouse database name.
        scope: Requested scope string ("clean", "issues", "all").
    Returns:
        Fully qualified ClickHouse table name.
    """
    # Cache the clean table name to avoid recomputing.
    clean_table = fact_table(settings)
    # Clean scope uses curated, validated records only.
    if scope == "clean":
        return clean_table
    # Issues scope isolates records with quality violations.
    if scope == "issues":
        return _issues_scope_table(settings)
    # Default is "all" which includes both clean and issue facts.
    return all_fact_table(settings)

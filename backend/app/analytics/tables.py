"""Analytics scope table helpers."""

from __future__ import annotations

from app.core.config import Settings
from app.db.clickhouse import all_fact_table, fact_table, issue_fact_table

SCOPE_VALUES = {"clean", "issues", "all"}

def _issues_scope_table(settings: Settings) -> str:
    """Return the ISSUE fact table name."""
    return issue_fact_table(settings)


def build_scope_table(settings: Settings, scope: str) -> str:
    """Resolve the ClickHouse table or derived scope for analytics queries."""
    clean_table = fact_table(settings)
    if scope == "clean":
        return clean_table
    if scope == "issues":
        return _issues_scope_table(settings)
    return all_fact_table(settings)

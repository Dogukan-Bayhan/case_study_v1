"""Query builder tests to guard sorting and tenant filters."""

from app.analytics.queries import build_timeseries_query, build_transactions_query


def test_timeseries_query_builder():
    """Ensure timeseries queries include tenant filter and grain.

    Business purpose:
        Validate query builder enforces tenant isolation and grain selection.
    Why it exists:
        Prevents regressions in analytics query construction.
    Where used:
        Test suite for analytics query builders.
    Inputs:
        None; constructs a query using known inputs.
    Returns:
        None; asserts query and params.
    """
    # Build a simple timeseries query and check key clauses.
    query, params = build_timeseries_query("revenue", "day", 10, None, "analytics.fact_transactions_clean")
    assert "toDate(event_ts)" in query
    assert "tenant_id = %(tenant_id)s" in query
    assert params["tenant_id"] == 10


def test_transactions_query_builder():
    """Ensure transaction query builder emits pagination and ordering clauses.

    Business purpose:
        Validate the SQL builder includes ORDER BY and pagination clauses.
    Why it exists:
        Guards against regressions in transaction query formatting.
    Where used:
        Test suite for analytics query builders.
    Inputs:
        None; constructs a query using known inputs.
    Returns:
        None; asserts query includes required clauses.
    """
    # Build a transaction query and check ordering/pagination.
    query, _ = build_transactions_query(10, None, "order_date", "desc", "analytics.fact_transactions_clean")
    assert "ORDER BY order_date DESC" in query
    assert "LIMIT %(limit)s OFFSET %(offset)s" in query

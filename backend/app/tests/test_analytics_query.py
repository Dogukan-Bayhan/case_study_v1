"""Query builder tests to guard sorting and tenant filters."""

from app.analytics.queries import build_timeseries_query, build_transactions_query


def test_timeseries_query_builder():
    """Ensure the timeseries query includes the tenant filter and grain."""
    query = build_timeseries_query("revenue", "day", 10, None, "analytics.fact_transactions_clean")
    assert "toDate(event_ts)" in query
    assert "tenant_id = 10" in query


def test_transactions_query_builder():
    """Ensure the transactions query uses the expected ORDER/LIMIT/OFFSET."""
    query = build_transactions_query(10, None, "order_date", "desc", "analytics.fact_transactions_clean")
    assert "ORDER BY order_date DESC" in query
    assert "LIMIT %(limit)s OFFSET %(offset)s" in query

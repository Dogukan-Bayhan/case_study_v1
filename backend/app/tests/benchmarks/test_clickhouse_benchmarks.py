"""ClickHouse query performance benchmarks."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

from .utils import print_metrics, print_summary, run_query_with_metrics, summarize_metrics


BENCH_ITERATIONS = int(os.getenv("BENCHMARK_ITERATIONS", "5"))
PAGE_SIZE = int(os.getenv("BENCHMARK_PAGE_SIZE", "100"))


def _run_benchmark(client, label: str, query: str, params: dict | None = None) -> list:
    """Run a benchmark query multiple times and record warm/cold metrics."""
    runs = []
    for idx in range(BENCH_ITERATIONS):
        metrics, _ = run_query_with_metrics(client, query, params)
        run_type = "cold" if idx == 0 else "warm"
        print_metrics(label, run_type, metrics)
        runs.append(metrics)
    warm_runs = runs[1:]
    if warm_runs:
        summary = summarize_metrics(warm_runs)
        print_summary(label, summary)
    return runs


def _select_primary_tenant(client, table: str) -> tuple[int, int]:
    """Pick the busiest tenant to produce stable benchmark results."""
    rows = client.execute(
        f"""
        SELECT tenant_id, count() AS total
        FROM {table}
        GROUP BY tenant_id
        ORDER BY total DESC
        LIMIT 1
        """
    )
    if not rows:
        pytest.skip("No tenant data found in ClickHouse.")
    tenant_id, total = rows[0]
    return int(tenant_id), int(total)


def _tenant_date_range(client, table: str, tenant_id: int):
    """Return min/max order_date for a tenant or None if missing."""
    rows = client.execute(
        f"""
        SELECT min(order_date), max(order_date)
        FROM {table}
        WHERE tenant_id = %(tenant_id)s AND order_date IS NOT NULL
        """,
        {"tenant_id": tenant_id},
    )
    if not rows:
        return None
    start, end = rows[0]
    if start is None or end is None:
        return None
    return start, end


def test_benchmark_clickhouse_count_all(clickhouse_client, clickhouse_fact_table):
    """
    Benchmark COUNT(*) query execution time and resource usage.

    Why: COUNT(*) is common for pagination totals and monitoring; it stresses scan performance.
    Expected: query completes successfully with stable warm-run timings.
    Failure indicates: table access or ClickHouse performance regressions.
    """
    query = f"SELECT count() FROM {clickhouse_fact_table}"
    runs = _run_benchmark(clickhouse_client, "clickhouse_count_all", query)
    assert runs, "COUNT(*) benchmark did not execute."


def test_benchmark_clickhouse_pagination_query(clickhouse_client, clickhouse_fact_table):
    """
    Benchmark a tenant-scoped pagination query (LIMIT + OFFSET).

    Why: Pagination drives the Transactions UI and must stay responsive on large datasets.
    Expected: query completes successfully and returns PAGE_SIZE rows on warm runs.
    Failure indicates: degraded ClickHouse query latency or pagination instability.
    """
    tenant_id, total_rows = _select_primary_tenant(clickhouse_client, clickhouse_fact_table)
    if total_rows <= PAGE_SIZE:
        pytest.skip("Not enough rows to benchmark pagination with OFFSET.")
    offset = min(total_rows - PAGE_SIZE, max(PAGE_SIZE, total_rows // 2))
    query = f"""
        SELECT transaction_id, order_date, total_amount
        FROM {clickhouse_fact_table}
        WHERE tenant_id = %(tenant_id)s
        ORDER BY order_date DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """
    params = {"tenant_id": tenant_id, "limit": PAGE_SIZE, "offset": offset}
    runs = _run_benchmark(clickhouse_client, "clickhouse_pagination", query, params)
    assert runs[-1].result_rows > 0, "Pagination benchmark returned no rows."


def test_benchmark_clickhouse_aggregation_query(clickhouse_client, clickhouse_fact_table):
    """
    Benchmark aggregation performance (SUM + GROUP BY).

    Why: KPI and top-product queries depend on aggregation speed in ClickHouse.
    Expected: query completes successfully with stable warm-run timings.
    Failure indicates: aggregation or grouping performance regression.
    """
    tenant_id, _ = _select_primary_tenant(clickhouse_client, clickhouse_fact_table)
    query = f"""
        SELECT department, sum(amount) AS revenue
        FROM {clickhouse_fact_table}
        WHERE tenant_id = %(tenant_id)s
        GROUP BY department
        ORDER BY revenue DESC
        LIMIT 50
    """
    params = {"tenant_id": tenant_id}
    runs = _run_benchmark(clickhouse_client, "clickhouse_aggregation", query, params)
    assert runs, "Aggregation benchmark did not execute."


def test_benchmark_clickhouse_filtered_date_query(clickhouse_client, clickhouse_fact_table):
    """
    Benchmark a filtered query (tenant + date range).

    Why: time-based filtering is typical for analytics dashboards.
    Expected: query completes successfully with stable warm-run timings.
    Failure indicates: regression in predicate filtering or date parsing.
    """
    tenant_id, _ = _select_primary_tenant(clickhouse_client, clickhouse_fact_table)
    date_range = _tenant_date_range(clickhouse_client, clickhouse_fact_table, tenant_id)
    if date_range is None:
        pytest.skip("No order_date values available for date-range benchmark.")
    start, end = date_range
    midpoint = start + (end - start) / 2
    window_start = midpoint - timedelta(days=7)
    window_end = midpoint + timedelta(days=7)

    query = f"""
        SELECT count(), sum(amount) AS revenue
        FROM {clickhouse_fact_table}
        WHERE tenant_id = %(tenant_id)s
          AND order_date >= %(start)s
          AND order_date < %(end)s
    """
    params = {"tenant_id": tenant_id, "start": window_start, "end": window_end}
    runs = _run_benchmark(clickhouse_client, "clickhouse_filtered_date", query, params)
    assert runs, "Filtered date benchmark did not execute."

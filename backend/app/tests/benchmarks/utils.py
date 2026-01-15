"""Helpers for benchmarking ClickHouse queries."""

from __future__ import annotations

import statistics
import time
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class QueryMetrics:
    """Snapshot of query performance metrics gathered from ClickHouse."""
    query_id: str
    elapsed_seconds: float
    rows_processed: int | None
    read_rows: int | None
    read_bytes: int | None
    result_rows: int
    memory_usage_bytes: int | None
    query_duration_ms: int | None


BENCHMARK_RUNS: list[dict[str, object]] = []
BENCHMARK_SUMMARIES: list[dict[str, object]] = []
CONCURRENCY_SUMMARIES: list[dict[str, object]] = []


def _fetch_query_log_metrics(
    client,
    query_id: str,
    timeout_seconds: float = 2.0,
    poll_interval_seconds: float = 0.2,
) -> dict[str, int] | None:
    """Poll the ClickHouse query log for detailed metrics by query id."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        rows = client.execute(
            """
            SELECT read_rows, read_bytes, result_rows, memory_usage, query_duration_ms
            FROM system.query_log
            WHERE query_id = %(query_id)s AND type = 'QueryFinish'
            ORDER BY event_time_microseconds DESC
            LIMIT 1
            """,
            {"query_id": query_id},
        )
        if rows:
            read_rows, read_bytes, result_rows, memory_usage, query_duration_ms = rows[0]
            return {
                "read_rows": int(read_rows) if read_rows is not None else None,
                "read_bytes": int(read_bytes) if read_bytes is not None else None,
                "result_rows": int(result_rows) if result_rows is not None else None,
                "memory_usage": int(memory_usage) if memory_usage is not None else None,
                "query_duration_ms": int(query_duration_ms) if query_duration_ms is not None else None,
            }
        time.sleep(poll_interval_seconds)
    return None


def run_query_with_metrics(client, query: str, params: dict | None = None) -> tuple[QueryMetrics, list]:
    """Execute a query and return metrics plus result rows."""
    query_id = f"bench-{uuid.uuid4().hex}"
    start = time.perf_counter()
    result = client.execute(query, params or {}, settings={"query_id": query_id})
    elapsed = time.perf_counter() - start
    result_rows = len(result)

    log_metrics = _fetch_query_log_metrics(client, query_id)
    read_rows = log_metrics["read_rows"] if log_metrics else None
    read_bytes = log_metrics["read_bytes"] if log_metrics else None
    memory_usage = log_metrics["memory_usage"] if log_metrics else None
    query_duration_ms = log_metrics["query_duration_ms"] if log_metrics else None
    rows_processed = read_rows if read_rows is not None else result_rows

    metrics = QueryMetrics(
        query_id=query_id,
        elapsed_seconds=elapsed,
        rows_processed=rows_processed,
        read_rows=read_rows,
        read_bytes=read_bytes,
        result_rows=result_rows,
        memory_usage_bytes=memory_usage,
        query_duration_ms=query_duration_ms,
    )
    return metrics, result


def summarize_metrics(metrics: list[QueryMetrics]) -> dict[str, float]:
    """Summarize elapsed time metrics for reporting."""
    elapsed_values = [item.elapsed_seconds for item in metrics]
    if not elapsed_values:
        return {"min": 0.0, "avg": 0.0, "max": 0.0, "p95": 0.0}
    elapsed_sorted = sorted(elapsed_values)
    p95_index = max(0, int(round(0.95 * (len(elapsed_sorted) - 1))))
    return {
        "min": min(elapsed_values),
        "avg": statistics.mean(elapsed_values),
        "max": max(elapsed_values),
        "p95": elapsed_sorted[p95_index],
    }


def print_metrics(label: str, run_type: str, metrics: QueryMetrics) -> None:
    """Print a single-line metrics record for easy parsing."""
    BENCHMARK_RUNS.append(
        {
            "label": label,
            "run": run_type,
            "elapsed": metrics.elapsed_seconds,
            "rows_processed": metrics.rows_processed,
            "read_rows": metrics.read_rows,
            "read_bytes": metrics.read_bytes,
            "result_rows": metrics.result_rows,
            "memory_bytes": metrics.memory_usage_bytes,
            "query_duration_ms": metrics.query_duration_ms,
        }
    )
    print(format_benchmark_run(label, run_type, metrics))


def print_summary(label: str, summary: dict[str, float]) -> None:
    """Print summary metrics for warm runs."""
    BENCHMARK_SUMMARIES.append(
        {
            "label": label,
            "min": summary["min"],
            "avg": summary["avg"],
            "max": summary["max"],
            "p95": summary["p95"],
        }
    )
    print(format_benchmark_summary(label, summary))


def record_concurrency_summary(tenant: str, avg: float, p95: float, samples: int) -> None:
    """Record summary stats for concurrent tenant benchmarks."""
    CONCURRENCY_SUMMARIES.append(
        {"tenant": tenant, "avg": avg, "p95": p95, "samples": samples}
    )


def format_benchmark_run(label: str, run_type: str, metrics: QueryMetrics) -> str:
    """Format a single benchmark run in a stable, parseable line."""
    return (
        f"[BENCH] {label} run={run_type} "
        f"elapsed={metrics.elapsed_seconds:.4f}s "
        f"rows_processed={metrics.rows_processed} "
        f"read_rows={metrics.read_rows} "
        f"read_bytes={metrics.read_bytes} "
        f"result_rows={metrics.result_rows} "
        f"memory_bytes={metrics.memory_usage_bytes} "
        f"query_duration_ms={metrics.query_duration_ms}"
    )


def format_benchmark_summary(label: str, summary: dict[str, float]) -> str:
    """Format aggregate warm-run metrics for console output."""
    return (
        f"[BENCH] {label} warm_summary "
        f"min={summary['min']:.4f}s avg={summary['avg']:.4f}s "
        f"max={summary['max']:.4f}s p95={summary['p95']:.4f}s"
    )


def format_concurrency_summary(record: dict[str, object]) -> str:
    """Format concurrency results for console output."""
    return (
        "[CONCURRENCY] "
        f"tenant={record['tenant']} "
        f"avg={record['avg']:.4f}s "
        f"p95={record['p95']:.4f}s "
        f"samples={record['samples']}"
    )

# Benchmark Suite

This benchmark and validation suite targets ClickHouse query performance and multi-tenant behavior for the running analytics system. It uses only the public API and ClickHouse, and does not modify data.

## What is measured

ClickHouse query benchmarks:
- COUNT(*) on `fact_transactions_clean`
- Pagination query (tenant-scoped LIMIT + OFFSET)
- Aggregation query (SUM + GROUP BY)
- Filtered query (tenant + date range)

Multi-tenant validation:
- Tenant isolation (rows returned by the API must exist under the same tenant in ClickHouse)
- Tenant result-set overlap detection (flags unexpected overlap)
- Pagination consistency per tenant
- Concurrent tenant load latency

## Metrics captured

Each ClickHouse benchmark prints:
- `elapsed` (client-measured wall time)
- `rows_processed` (uses ClickHouse `read_rows` when available, otherwise falls back to result row count)
- `read_rows`, `read_bytes`, `result_rows` (from `system.query_log` when available)
- `memory_bytes` (if ClickHouse exposes memory usage for the query)
- `query_duration_ms` (ClickHouse-reported runtime)

Cold vs warm:
- The first run of each benchmark is labeled `cold`
- Subsequent runs are labeled `warm`, and a warm summary (min/avg/max/p95) is printed

## How to interpret results

- Compare warm-run summaries across runs to detect regressions.
- Large cold-to-warm gaps indicate cache-sensitive workloads.
- High `read_rows` or `read_bytes` values often explain slow queries.
- Concurrency output prints per-tenant average and p95 latency; p95 above the threshold suggests degradation.

## Known limitations

- ClickHouse `system.query_log` availability depends on server settings; if disabled, memory/row metrics may be missing.
- No cache flush is performed; the "cold" run is simply the first execution in the test process.
- Performance varies with data size, host resources, Docker storage, and concurrent activity.
- Tenant overlap tests skip when the same dataset is loaded across multiple tenants.

## How to run

Run benchmarks only:

```bash
docker compose exec api pytest backend/app/tests/benchmarks
```

### Optional environment variables

- `ANALYTICS_API_BASE_URL` (default: `http://localhost:8000`)
- `ANALYTICS_API_TIMEOUT` (default: `20`)
- `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, `CLICKHOUSE_DATABASE`
- `BENCHMARK_ITERATIONS` (default: `5`)
- `BENCHMARK_PAGE_SIZE` (default: `100`)
- `QA_CONCURRENT_REQUESTS_PER_TENANT` (default: `5`)
- `QA_MAX_LATENCY_SECONDS` (default: `10`)
- `QA_TENANT_PAGE_SIZE` (default: `50`)

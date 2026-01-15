"""Multi-tenant isolation, pagination, and concurrency tests."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from .utils import record_concurrency_summary

MAX_LATENCY_SECONDS = float(os.getenv("QA_MAX_LATENCY_SECONDS", "10"))
CONCURRENT_REQUESTS_PER_TENANT = int(os.getenv("QA_CONCURRENT_REQUESTS_PER_TENANT", "5"))
PAGE_SIZE = int(os.getenv("QA_TENANT_PAGE_SIZE", "50"))


def _auth_headers(token: str) -> dict[str, str]:
    """Build Authorization headers for API calls."""
    return {"Authorization": f"Bearer {token}"}


def _get_me(api_client, headers: dict[str, str]) -> dict[str, str]:
    """Fetch /auth/me to identify the current tenant."""
    response = api_client.get("/auth/me", headers=headers)
    response.raise_for_status()
    return response.json()


def _tenant_id_from_me(me: dict[str, object]) -> int:
    """Extract tenant_id from the /auth/me payload."""
    tenant_id = me.get("tenant_id", me.get("tenantId"))
    if tenant_id is None:
        raise RuntimeError(f"Expected tenant identifier in /auth/me response: {me}")
    return int(tenant_id)


def _get_transactions(api_client, headers: dict[str, str], page: int, page_size: int) -> dict[str, object]:
    """Fetch a page of transactions with stable sort ordering."""
    response = api_client.get(
        "/analytics/transactions",
        headers=headers,
        params={"page": page, "page_size": page_size, "sort_by": "order_date", "sort_dir": "desc"},
    )
    response.raise_for_status()
    return response.json()


def _timed_transactions(api_client, headers: dict[str, str], page: int, page_size: int) -> tuple[float, dict]:
    """Measure the latency of a transactions request."""
    start = time.perf_counter()
    payload = _get_transactions(api_client, headers, page, page_size)
    elapsed = time.perf_counter() - start
    return elapsed, payload


def _query_ids_for_tenant(clickhouse_client, table: str, tenant_id: int, ids: list[str]) -> set[str]:
    """Validate transaction IDs exist for a tenant in ClickHouse."""
    if not ids:
        return set()
    rows = clickhouse_client.execute(
        f"""
        SELECT transaction_id
        FROM {table}
        WHERE tenant_id = %(tenant_id)s AND transaction_id IN %(ids)s
        """,
        {"tenant_id": tenant_id, "ids": tuple(ids)},
    )
    return {row[0] for row in rows}


def test_tenant_rows_belong_to_clickhouse_tenant(
    api_client,
    token_factory,
    clickhouse_client,
    clickhouse_fact_table,
):
    """
    Ensure API rows returned for each tenant exist in ClickHouse for that tenant.

    Why: Verifies tenant scoping at the API layer against the source of truth.
    Expected: every transaction_id returned by the API exists under the same tenant_id in ClickHouse.
    Failure indicates: tenant filter missing or API returning rows from the wrong tenant.
    """
    for user_key in ("alpha_admin", "beta_admin"):
        token = token_factory(user_key)
        headers = _auth_headers(token)
        me = _get_me(api_client, headers)
        tenant_id = _tenant_id_from_me(me)

        payload = _get_transactions(api_client, headers, page=1, page_size=PAGE_SIZE)
        ids = [row["transactionId"] for row in payload["rows"]]
        if not ids:
            pytest.skip(f"No rows returned for tenant {tenant_id}.")

        found = _query_ids_for_tenant(clickhouse_client, clickhouse_fact_table, tenant_id, ids)
        missing = [tid for tid in ids if tid not in found]
        assert not missing, f"API returned rows not found in ClickHouse for tenant {tenant_id}: {missing[:5]}"


def test_tenant_result_sets_do_not_overlap_unless_data_shared(
    api_client,
    token_factory,
    clickhouse_client,
    clickhouse_fact_table,
):
    """
    Compare tenant result sets to detect potential cross-tenant leakage.

    Why: If tenant A sees tenant B rows, transaction IDs will overlap where data is not shared.
    Expected: overlap is empty, or any overlap corresponds to rows that exist in both tenants.
    Failure indicates: tenant leakage in API results.
    """
    alpha_token = token_factory("alpha_admin")
    beta_token = token_factory("beta_admin")
    alpha_headers = _auth_headers(alpha_token)
    beta_headers = _auth_headers(beta_token)

    alpha_me = _get_me(api_client, alpha_headers)
    beta_me = _get_me(api_client, beta_headers)
    alpha_tenant = _tenant_id_from_me(alpha_me)
    beta_tenant = _tenant_id_from_me(beta_me)

    alpha_rows = _get_transactions(api_client, alpha_headers, page=1, page_size=PAGE_SIZE)["rows"]
    beta_rows = _get_transactions(api_client, beta_headers, page=1, page_size=PAGE_SIZE)["rows"]

    if not alpha_rows or not beta_rows:
        pytest.skip("Not enough rows to compare tenant result sets.")

    alpha_ids = {row["transactionId"] for row in alpha_rows}
    beta_ids = {row["transactionId"] for row in beta_rows}
    overlap = sorted(alpha_ids.intersection(beta_ids))

    if not overlap:
        return

    overlap = overlap[:50]
    alpha_found = _query_ids_for_tenant(clickhouse_client, clickhouse_fact_table, alpha_tenant, overlap)
    beta_found = _query_ids_for_tenant(clickhouse_client, clickhouse_fact_table, beta_tenant, overlap)

    leaked = [tid for tid in overlap if tid not in alpha_found or tid not in beta_found]
    if leaked:
        raise AssertionError(f"Potential tenant leakage detected for transaction IDs: {leaked}")

    pytest.skip("Overlap detected but rows exist in both tenants; data appears shared.")


def test_pagination_consistency_per_tenant(api_client, token_factory):
    """
    Ensure pagination is consistent and non-overlapping per tenant.

    Why: Each tenant should page independently without duplication or skipped rows.
    Expected: page 1 and page 2 have no overlapping transaction IDs for each tenant.
    Failure indicates: broken offset logic or unstable ordering.
    """
    for user_key in ("alpha_admin", "beta_admin"):
        token = token_factory(user_key)
        headers = _auth_headers(token)
        page_one = _get_transactions(api_client, headers, page=1, page_size=PAGE_SIZE)
        total = int(page_one["total"])
        if total < PAGE_SIZE * 2:
            pytest.skip(f"Not enough rows to validate pagination for {user_key}.")
        page_two = _get_transactions(api_client, headers, page=2, page_size=PAGE_SIZE)

        page_one_ids = {row["transactionId"] for row in page_one["rows"]}
        page_two_ids = {row["transactionId"] for row in page_two["rows"]}
        overlap = page_one_ids.intersection(page_two_ids)
        assert not overlap, f"Pagination overlap detected for {user_key}: {sorted(overlap)[:5]}"


def test_concurrent_tenant_load_latency(api_client, token_factory):
    """
    Simulate concurrent tenant traffic and record per-tenant latency.

    Why: Concurrent tenant traffic should not cause errors or extreme latency spikes.
    Expected: all requests succeed and latency stays below QA_MAX_LATENCY_SECONDS.
    Failure indicates: instability under parallel load or tenant-level degradation.
    """
    alpha_token = token_factory("alpha_admin")
    beta_token = token_factory("beta_admin")
    alpha_headers = _auth_headers(alpha_token)
    beta_headers = _auth_headers(beta_token)

    results = {"alpha": [], "beta": []}
    errors = []

    with ThreadPoolExecutor(max_workers=CONCURRENT_REQUESTS_PER_TENANT * 2) as executor:
        future_map = {}
        for _ in range(CONCURRENT_REQUESTS_PER_TENANT):
            alpha_future = executor.submit(_timed_transactions, api_client, alpha_headers, 1, PAGE_SIZE)
            beta_future = executor.submit(_timed_transactions, api_client, beta_headers, 1, PAGE_SIZE)
            future_map[alpha_future] = "alpha"
            future_map[beta_future] = "beta"

        for future in as_completed(future_map):
            tenant_label = future_map[future]
            try:
                elapsed, payload = future.result()
                tenant_total = int(payload.get("total", 0))
                if tenant_total == 0:
                    pytest.skip("No data returned during concurrency test.")
                if payload.get("rows") is None:
                    raise RuntimeError("Missing rows in API response.")
                results[tenant_label].append(elapsed)
            except Exception as exc:  # pragma: no cover - concurrency diagnostics
                errors.append(f"{tenant_label} error: {exc}")

    assert not errors, f"Errors during concurrent tenant load: {errors}"

    for tenant, latencies in results.items():
        if not latencies:
            pytest.skip(f"No latency data collected for {tenant}.")
        latencies_sorted = sorted(latencies)
        p95_index = max(0, int(round(0.95 * (len(latencies_sorted) - 1))))
        p95 = latencies_sorted[p95_index]
        avg = sum(latencies) / len(latencies)
        record_concurrency_summary(tenant, avg, p95, len(latencies))
        assert p95 <= MAX_LATENCY_SECONDS, (
            f"Tenant {tenant} p95 latency {p95:.2f}s exceeds {MAX_LATENCY_SECONDS:.2f}s."
        )

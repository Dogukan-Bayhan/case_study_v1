"""API pagination behavior tests for the transactions endpoint."""

from __future__ import annotations

import pytest


def _auth_headers(token: str) -> dict[str, str]:
    """Build Authorization headers for API requests."""
    return {"Authorization": f"Bearer {token}"}


def _get_me(api_client, headers: dict[str, str]) -> dict[str, str]:
    """Fetch /auth/me to determine the active tenant."""
    response = api_client.get("/auth/me", headers=headers)
    response.raise_for_status()
    return response.json()


def _tenant_id_from_me(me: dict[str, object]) -> int:
    """Extract tenant_id from the /auth/me payload."""
    tenant_id = me.get("tenant_id", me.get("tenantId"))
    if tenant_id is None:
        raise RuntimeError(f"Expected tenant identifier in /auth/me response: {me}")
    return int(tenant_id)


def _get_transactions(api_client, headers: dict[str, str], params: dict[str, int | str]) -> dict[str, object]:
    """Fetch transactions with explicit pagination parameters."""
    response = api_client.get("/analytics/transactions", headers=headers, params=params)
    response.raise_for_status()
    return response.json()


def test_transactions_pagination_offset_matches_clickhouse(
    api_client,
    token_factory,
    clickhouse_client,
    clickhouse_fact_table,
):
    """
    Validate that API pagination offset matches the expected ClickHouse slice.

    Why: Incorrect offset calculations can silently duplicate or skip rows.
    Failure caught: API returning the wrong records for page N.
    Expected: page 2 results match ClickHouse LIMIT/OFFSET results for the same tenant.
    """
    token = token_factory("alpha_admin")
    headers = _auth_headers(token)
    me = _get_me(api_client, headers)
    tenant_id = _tenant_id_from_me(me)

    page_size = 25
    total_rows = clickhouse_client.execute(
        f"SELECT count() FROM {clickhouse_fact_table} WHERE tenant_id = %(tenant_id)s",
        {"tenant_id": tenant_id},
    )[0][0]
    if total_rows < page_size * 2:
        pytest.skip("Not enough rows to validate page 2 offset behavior.")

    expected = clickhouse_client.execute(
        f"""
        SELECT transaction_id
        FROM {clickhouse_fact_table}
        WHERE tenant_id = %(tenant_id)s
        ORDER BY order_date DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {"tenant_id": tenant_id, "limit": page_size, "offset": page_size},
    )
    expected_ids = [row[0] for row in expected]

    payload = _get_transactions(
        api_client,
        headers,
        {
            "page": 2,
            "page_size": page_size,
            "sort_by": "order_date",
            "sort_dir": "desc",
        },
    )
    api_ids = [row["transactionId"] for row in payload["rows"]]

    assert api_ids == expected_ids, "API pagination does not align with ClickHouse offset results."


def test_transactions_pages_are_non_overlapping(api_client, token_factory):
    """
    Ensure consecutive pages do not overlap and maintain consistent ordering.

    Why: Overlap between pages indicates broken pagination or unstable ordering.
    Failure caught: duplicated rows across pages or inconsistent page boundaries.
    Expected: page 1 and page 2 have no shared transaction IDs.
    """
    token = token_factory("alpha_admin")
    headers = _auth_headers(token)

    page_size = 20
    page_one = _get_transactions(
        api_client,
        headers,
        {"page": 1, "page_size": page_size, "sort_by": "order_date", "sort_dir": "desc"},
    )
    total = int(page_one["total"])
    if total < page_size * 2:
        pytest.skip("Not enough rows to validate non-overlapping pages.")

    page_two = _get_transactions(
        api_client,
        headers,
        {"page": 2, "page_size": page_size, "sort_by": "order_date", "sort_dir": "desc"},
    )
    page_one_ids = {row["transactionId"] for row in page_one["rows"]}
    page_two_ids = {row["transactionId"] for row in page_two["rows"]}

    overlap = page_one_ids.intersection(page_two_ids)
    assert not overlap, f"Pagination overlap detected between pages: {sorted(overlap)[:5]}"


def test_page_size_alias_overrides_page_size_param(api_client, token_factory):
    """
    Verify that pageSize (camelCase) overrides page_size when both are provided.

    Why: The API supports both styles; regressions here break client compatibility.
    Failure caught: pageSize ignored or misapplied when both parameters are present.
    Expected: response pageSize reflects the camelCase parameter and row count respects it.
    """
    token = token_factory("alpha_admin")
    headers = _auth_headers(token)

    payload = _get_transactions(
        api_client,
        headers,
        {"page": 1, "page_size": 10, "pageSize": 5, "sort_by": "order_date", "sort_dir": "desc"},
    )
    assert int(payload["pageSize"]) == 5, "pageSize should override page_size when both are supplied."
    assert len(payload["rows"]) <= 5, "Row count should respect the overridden pageSize value."

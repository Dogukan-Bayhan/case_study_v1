"""API pagination behavior tests for the transactions endpoint."""

from __future__ import annotations

import pytest


def _auth_headers(token: str) -> dict[str, str]:
    """Build Authorization headers for API requests.

    Business purpose:
        Provide bearer auth headers for QA API calls.
    Why it exists:
        Keeps header construction consistent across tests.
    Where used:
        QA pagination tests.
    Inputs:
        token: JWT access token.
    Returns:
        Dict containing Authorization header.
    """
    return {"Authorization": f"Bearer {token}"}


def _get_me(api_client, headers: dict[str, str]) -> dict[str, str]:
    """Fetch /auth/me to determine the active tenant.

    Business purpose:
        Resolve tenant identity for pagination validation.
    Why it exists:
        /auth/me is the source of truth for tenant context.
    Where used:
        QA pagination tests.
    Inputs:
        api_client: HTTP client for API requests.
        headers: Authorization headers.
    Returns:
        Parsed JSON payload from /auth/me.
    """
    response = api_client.get("/auth/me", headers=headers)
    response.raise_for_status()
    return response.json()


def _tenant_id_from_me(me: dict[str, object]) -> int:
    """Extract tenant_id from the /auth/me payload.

    Business purpose:
        Normalize tenant_id extraction for assertions.
    Why it exists:
        Payload may use snake_case or camelCase fields.
    Where used:
        QA pagination tests.
    Inputs:
        me: /auth/me payload dict.
    Returns:
        Tenant id as an integer.
    """
    tenant_id = me.get("tenant_id", me.get("tenantId"))
    if tenant_id is None:
        raise RuntimeError(f"Expected tenant identifier in /auth/me response: {me}")
    return int(tenant_id)


def _get_transactions(api_client, headers: dict[str, str], params: dict[str, int | str]) -> dict[str, object]:
    """Fetch transactions with explicit pagination parameters.

    Business purpose:
        Retrieve paged results for pagination validation.
    Why it exists:
        Provides a consistent request path for pagination tests.
    Where used:
        QA pagination tests.
    Inputs:
        api_client: HTTP client for API requests.
        headers: Authorization headers.
        params: Query parameters for pagination and sorting.
    Returns:
        Parsed JSON payload from the transactions endpoint.
    """
    response = api_client.get("/analytics/transactions", headers=headers, params=params)
    response.raise_for_status()
    return response.json()


def test_transactions_pagination_offset_matches_clickhouse(
    api_client,
    token_factory,
    clickhouse_client,
    clickhouse_fact_table,
):
    """Validate API pagination offset matches the ClickHouse slice.

    Business purpose:
        Ensure API pagination aligns with ClickHouse ordering and offsets.
    Why it exists:
        Detects incorrect offset math or ordering regressions.
    Where used:
        QA pagination tests.
    Inputs:
        api_client: HTTP client for API requests.
        token_factory: Fixture to issue auth tokens.
        clickhouse_client: ClickHouse client for validation queries.
        clickhouse_fact_table: Fact table name for validation.
    Returns:
        None; asserts API page matches ClickHouse offset results.
    """
    token = token_factory("alpha_admin")
    headers = _auth_headers(token)
    me = _get_me(api_client, headers)
    tenant_id = _tenant_id_from_me(me)

    page_size = 25
    # Query counts total rows for the tenant to ensure enough data for paging.
    # Tenant filter keeps the count scoped and avoids scanning other tenants.
    # COUNT(*) returns a scalar result to minimize output size.
    total_rows = clickhouse_client.execute(
        f"SELECT count() FROM {clickhouse_fact_table} WHERE tenant_id = %(tenant_id)s",
        {"tenant_id": tenant_id},
    )[0][0]
    if total_rows < page_size * 2:
        pytest.skip("Not enough rows to validate page 2 offset behavior.")

    # Query returns the expected page based on LIMIT/OFFSET ordering.
    # ORDER BY + LIMIT/OFFSET mirrors the API pagination behavior.
    # Tenant filter aligns with partitioning for efficient scanning.
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
    """Ensure consecutive pages do not overlap and maintain ordering.

    Business purpose:
        Verify pagination produces distinct, ordered result sets.
    Why it exists:
        Detects duplication or unstable ordering across pages.
    Where used:
        QA pagination tests.
    Inputs:
        api_client: HTTP client for API requests.
        token_factory: Fixture to issue auth tokens.
    Returns:
        None; asserts no overlap between pages.
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
    """Verify pageSize overrides page_size when both are provided.

    Business purpose:
        Preserve backward compatibility with camelCase pagination params.
    Why it exists:
        Clients may send either page_size or pageSize.
    Where used:
        QA pagination tests.
    Inputs:
        api_client: HTTP client for API requests.
        token_factory: Fixture to issue auth tokens.
    Returns:
        None; asserts pageSize is honored.
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

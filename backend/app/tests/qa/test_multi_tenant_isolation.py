"""Multi-tenant isolation checks using API and ClickHouse."""

from __future__ import annotations

import pytest


def _auth_headers(token: str) -> dict[str, str]:
    """Build a standard Authorization header for API requests.

    Business purpose:
        Provide bearer auth headers for QA API calls.
    Why it exists:
        Keeps header construction consistent across tests.
    Where used:
        QA multi-tenant isolation tests.
    Inputs:
        token: JWT access token.
    Returns:
        Dict containing Authorization header.
    """
    return {"Authorization": f"Bearer {token}"}


def _get_me(api_client, headers: dict[str, str]) -> dict[str, str]:
    """Fetch the current user payload to resolve tenant/user identifiers.

    Business purpose:
        Determine tenant and user identifiers for scoping checks.
    Why it exists:
        /auth/me is the source of truth for the current session.
    Where used:
        QA multi-tenant isolation tests.
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
    """Extract a tenant id from the /auth/me response or fail fast.

    Business purpose:
        Normalize tenant_id extraction for assertions.
    Why it exists:
        Payload may use snake_case or camelCase fields.
    Where used:
        QA multi-tenant isolation tests.
    Inputs:
        me: /auth/me payload dict.
    Returns:
        Tenant id as an integer.
    """
    tenant_id = me.get("tenant_id", me.get("tenantId"))
    if tenant_id is None:
        raise RuntimeError(f"Expected tenant identifier in /auth/me response: {me}")
    return int(tenant_id)


def _get_transactions(api_client, headers: dict[str, str]) -> dict[str, object]:
    """Request a minimal page to read tenant-scoped totals from the API.

    Business purpose:
        Fetch totals for tenant-scoped comparisons.
    Why it exists:
        Avoids large payloads while retrieving totals.
    Where used:
        QA multi-tenant isolation tests.
    Inputs:
        api_client: HTTP client for API requests.
        headers: Authorization headers.
    Returns:
        Parsed JSON payload from the transactions endpoint.
    """
    response = api_client.get(
        "/analytics/transactions",
        headers=headers,
        params={"page": 1, "page_size": 1, "sort_by": "order_date", "sort_dir": "desc"},
    )
    response.raise_for_status()
    return response.json()


@pytest.mark.parametrize("user_key", ["alpha_admin", "beta_admin"])
def test_admin_tenant_totals_match_clickhouse(
    api_client,
    token_factory,
    clickhouse_client,
    clickhouse_fact_table,
    user_key,
):
    """Ensure tenant filters are enforced for admin users.

    Business purpose:
        Validate tenant-scoped totals for admin users.
    Why it exists:
        Prevents cross-tenant leakage in admin views.
    Where used:
        QA multi-tenant isolation tests.
    Inputs:
        api_client: HTTP client for API requests.
        token_factory: Fixture to issue auth tokens.
        clickhouse_client: ClickHouse client for validation queries.
        clickhouse_fact_table: Fact table name for validation.
        user_key: Parametrized user key for the test.
    Returns:
        None; asserts API total matches ClickHouse total.
    """
    token = token_factory(user_key)
    headers = _auth_headers(token)
    me = _get_me(api_client, headers)
    tenant_id = _tenant_id_from_me(me)

    api_total = int(_get_transactions(api_client, headers)["total"])
    # Query counts ClickHouse rows for the tenant to compare with API total.
    # Tenant filter keeps the count scoped and efficient.
    # COUNT(*) returns a single scalar for quick comparison.
    clickhouse_total = clickhouse_client.execute(
        f"SELECT count() FROM {clickhouse_fact_table} WHERE tenant_id = %(tenant_id)s",
        {"tenant_id": tenant_id},
    )[0][0]

    assert api_total == clickhouse_total, (
        f"Tenant {tenant_id} API total {api_total} does not match ClickHouse {clickhouse_total}."
    )


def test_normal_user_totals_match_owner_filter(
    api_client,
    token_factory,
    clickhouse_client,
    clickhouse_fact_table,
):
    """Validate owner-level row filtering for normal users within a tenant.

    Business purpose:
        Verify owner_user_id scoping for normal users.
    Why it exists:
        Prevents normal users from seeing other users' data.
    Where used:
        QA multi-tenant isolation tests.
    Inputs:
        api_client: HTTP client for API requests.
        token_factory: Fixture to issue auth tokens.
        clickhouse_client: ClickHouse client for validation queries.
        clickhouse_fact_table: Fact table name for validation.
    Returns:
        None; asserts API total matches owner-scoped ClickHouse total.
    """
    token = token_factory("alpha_user")
    headers = _auth_headers(token)
    me = _get_me(api_client, headers)
    tenant_id = _tenant_id_from_me(me)
    user_id = int(me["id"])

    api_total = int(_get_transactions(api_client, headers)["total"])
    # Query counts rows filtered by tenant and owner for comparison.
    # Tenant + owner filters align with order keys for efficient scans.
    # COUNT(*) keeps the result set minimal for QA checks.
    clickhouse_total = clickhouse_client.execute(
        f"""
        SELECT count()
        FROM {clickhouse_fact_table}
        WHERE tenant_id = %(tenant_id)s AND owner_user_id = %(owner_user_id)s
        """,
        {"tenant_id": tenant_id, "owner_user_id": user_id},
    )[0][0]

    assert api_total == clickhouse_total, (
        "Normal user API total does not match ClickHouse owner_user_id filter."
    )


def test_tenant_totals_are_not_global_when_multiple_tenants_exist(
    api_client,
    token_factory,
    clickhouse_client,
    clickhouse_fact_table,
):
    """Confirm API totals are tenant-scoped when multiple tenants have data.

    Business purpose:
        Ensure API totals do not reflect global counts.
    Why it exists:
        Detects missing tenant filters in analytics endpoints.
    Where used:
        QA multi-tenant isolation tests.
    Inputs:
        api_client: HTTP client for API requests.
        token_factory: Fixture to issue auth tokens.
        clickhouse_client: ClickHouse client for validation queries.
        clickhouse_fact_table: Fact table name for validation.
    Returns:
        None; asserts tenant totals are less than global totals.
    """
    # Query returns tenant count and global row count for comparison.
    # countDistinct validates multi-tenant data without row-level reads.
    # Global count serves as an upper bound for tenant-scoped totals.
    tenant_count, global_total = clickhouse_client.execute(
        f"SELECT countDistinct(tenant_id), count() FROM {clickhouse_fact_table}"
    )[0]
    if tenant_count < 2:
        pytest.skip("Only one tenant has data; global leakage check is not applicable.")

    for user_key in ("alpha_admin", "beta_admin"):
        token = token_factory(user_key)
        headers = _auth_headers(token)
        api_total = int(_get_transactions(api_client, headers)["total"])
        assert api_total < global_total, "Tenant-scoped API total should not equal global total."

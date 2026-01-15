"""Multi-tenant isolation checks using API and ClickHouse."""

from __future__ import annotations

import pytest


def _auth_headers(token: str) -> dict[str, str]:
    """Build a standard Authorization header for API requests."""
    return {"Authorization": f"Bearer {token}"}


def _get_me(api_client, headers: dict[str, str]) -> dict[str, str]:
    """Fetch the current user payload to resolve tenant/user identifiers."""
    response = api_client.get("/auth/me", headers=headers)
    response.raise_for_status()
    return response.json()


def _tenant_id_from_me(me: dict[str, object]) -> int:
    """Extract a tenant id from the /auth/me response or fail fast."""
    tenant_id = me.get("tenant_id", me.get("tenantId"))
    if tenant_id is None:
        raise RuntimeError(f"Expected tenant identifier in /auth/me response: {me}")
    return int(tenant_id)


def _get_transactions(api_client, headers: dict[str, str]) -> dict[str, object]:
    """Request a minimal page to read tenant-scoped totals from the API."""
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
    """
    Ensure tenant filters are enforced for admin users.

    Why: Admins should see all data for their own tenant and nothing else.
    Failure caught: missing tenant filter causing cross-tenant leakage.
    Expected: API total matches ClickHouse row count for that tenant.
    """
    token = token_factory(user_key)
    headers = _auth_headers(token)
    me = _get_me(api_client, headers)
    tenant_id = _tenant_id_from_me(me)

    api_total = int(_get_transactions(api_client, headers)["total"])
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
    """
    Validate owner-level row filtering for normal users within a tenant.

    Why: Normal users must be restricted to their own rows to preserve privacy.
    Failure caught: owner_user_id filter missing or incorrect.
    Expected: API total equals ClickHouse rows filtered by tenant_id and owner_user_id.
    """
    token = token_factory("alpha_user")
    headers = _auth_headers(token)
    me = _get_me(api_client, headers)
    tenant_id = _tenant_id_from_me(me)
    user_id = int(me["id"])

    api_total = int(_get_transactions(api_client, headers)["total"])
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
    """
    Confirm that API totals are tenant-scoped when multiple tenants have data.

    Why: Global totals would imply cross-tenant leakage in analytics endpoints.
    Failure caught: missing tenant filter returning global counts.
    Expected: each tenant's API total is less than the global total when multiple tenants exist.
    """
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

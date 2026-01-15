"""Analytics query services."""

from app.analytics.queries import (
    TRANSACTION_COLUMNS,
    build_timeseries_query,
    build_transactions_count_query,
    build_transactions_query,
)
from app.analytics.schemas import KPIs, TimeSeriesPoint, TopProduct, TransactionPage, TransactionRow


def get_kpis(client, table_name: str, tenant_id: int, owner_user_id: int | None) -> KPIs:
    """Aggregate tenant-scoped KPIs for the dashboard tiles."""
    where = "tenant_id = %(tenant_id)s"
    params = {"tenant_id": tenant_id}
    if owner_user_id is not None:
        where += " AND owner_user_id = %(owner_user_id)s"
        params["owner_user_id"] = owner_user_id

    result = client.execute(
        f"""
        SELECT
            sum(amount) AS revenue,
            count() AS orders,
            avg(amount) AS avg_order_value,
            uniq(user_id) AS unique_customers
        FROM {table_name}
        WHERE {where}
        """,
        params,
    )[0]

    revenue, orders, avg_order_value, unique_customers = result
    return KPIs(
        revenue=float(revenue or 0),
        orders=int(orders or 0),
        avg_order_value=float(avg_order_value or 0),
        unique_customers=int(unique_customers or 0),
    )


def get_timeseries(
    client, metric: str, grain: str, table_name: str, tenant_id: int, owner_user_id: int | None
) -> list[TimeSeriesPoint]:
    """Return time-series data scoped to tenant and optional owner."""
    query = build_timeseries_query(metric, grain, tenant_id, owner_user_id, table_name)
    rows = client.execute(query)
    return [TimeSeriesPoint(bucket=str(bucket), value=float(value or 0)) for bucket, value in rows]


def get_top_products(
    client, table_name: str, tenant_id: int, owner_user_id: int | None, limit: int
) -> list[TopProduct]:
    """Rank products by revenue with strict tenant filtering."""
    where = "tenant_id = %(tenant_id)s"
    params = {"tenant_id": tenant_id, "limit": limit}
    if owner_user_id is not None:
        where += " AND owner_user_id = %(owner_user_id)s"
        params["owner_user_id"] = owner_user_id

    rows = client.execute(
        f"""
        SELECT product_id, sum(amount) AS revenue
        FROM {table_name}
        WHERE {where}
        GROUP BY product_id
        ORDER BY revenue DESC
        LIMIT %(limit)s
        """,
        params,
    )
    return [TopProduct(product_id=str(pid), revenue=float(rev or 0)) for pid, rev in rows]


def get_transactions(
    client,
    table_name: str,
    tenant_id: int,
    owner_user_id: int | None,
    page: int,
    page_size: int,
    sort_by: str,
    sort_dir: str,
) -> TransactionPage:
    """Fetch a page of transaction rows and total count for pagination."""
    # Offset math must match the UI to avoid gaps and duplicates.
    offset = (page - 1) * page_size
    params = {"tenant_id": tenant_id, "limit": page_size, "offset": offset}
    if owner_user_id is not None:
        params["owner_user_id"] = owner_user_id

    query = build_transactions_query(tenant_id, owner_user_id, sort_by, sort_dir, table_name)
    rows = client.execute(query, params)

    count_query = build_transactions_count_query(tenant_id, owner_user_id, table_name)
    total = int(client.execute(count_query, params)[0][0])

    results = []
    for row in rows:
        record = dict(zip(TRANSACTION_COLUMNS, row))
        record["order_date"] = record["order_date"].isoformat() if record["order_date"] else None
        results.append(TransactionRow(**record))

    return TransactionPage(page=page, page_size=page_size, total=total, rows=results)

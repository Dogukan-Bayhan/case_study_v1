"""Analytics query services."""

from app.analytics.queries import (
    TRANSACTION_COLUMNS,
    build_breakdown_query,
    build_top_products_query,
    build_where_clause,
    build_timeseries_query,
    build_transactions_count_query,
    build_transactions_query,
)
from app.analytics.schemas import (
    BreakdownRow,
    CustomerSegment,
    KPIs,
    TimeSeriesPoint,
    TopProduct,
    TransactionPage,
    TransactionRow,
)


def get_kpis(
    client,
    table_name: str,
    tenant_id: int,
    owner_user_id: int | None,
    filters: dict[str, object] | None = None,
) -> KPIs:
    """Aggregate tenant-scoped KPIs for the dashboard tiles."""
    where, params = build_where_clause(tenant_id, owner_user_id, filters)

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
    client,
    metric: str,
    grain: str,
    table_name: str,
    tenant_id: int,
    owner_user_id: int | None,
    filters: dict[str, object] | None = None,
) -> list[TimeSeriesPoint]:
    """Return time-series data scoped to tenant and optional owner."""
    query, params = build_timeseries_query(metric, grain, tenant_id, owner_user_id, table_name, filters)
    rows = client.execute(query, params)
    return [TimeSeriesPoint(bucket=str(bucket), value=float(value or 0)) for bucket, value in rows]


def get_top_products(
    client,
    table_name: str,
    tenant_id: int,
    owner_user_id: int | None,
    limit: int,
    metric: str = "revenue",
    filters: dict[str, object] | None = None,
) -> list[TopProduct]:
    """Rank products by revenue with strict tenant filtering."""
    query, params = build_top_products_query(
        tenant_id,
        owner_user_id,
        table_name,
        limit,
        metric,
        filters,
    )
    rows = client.execute(query, params)
    return [
        TopProduct(product=str(product), metric=metric, value=float(value or 0))
        for product, value in rows
    ]


def get_breakdown(
    client,
    table_name: str,
    tenant_id: int,
    owner_user_id: int | None,
    dimension: str,
    limit: int,
    filters: dict[str, object] | None = None,
) -> list[BreakdownRow]:
    """Return aggregated breakdown rows for a given dimension."""
    query, params = build_breakdown_query(
        dimension,
        tenant_id,
        owner_user_id,
        table_name,
        limit,
        filters,
    )
    rows = client.execute(query, params)
    return [
        BreakdownRow(
            key=str(key) if key is not None else "Unknown",
            revenue=float(revenue or 0),
            orders=int(orders or 0),
            avg_order_value=float(avg_order_value or 0),
            quantity=float(quantity or 0),
        )
        for key, revenue, orders, avg_order_value, quantity in rows
    ]


def get_customer_segments(
    client,
    table_name: str,
    tenant_id: int,
    owner_user_id: int | None,
    filters: dict[str, object] | None = None,
) -> list[CustomerSegment]:
    """Aggregate new vs returning customer metrics."""
    where, params = build_where_clause(tenant_id, owner_user_id, filters)
    row = client.execute(
        f"""
        SELECT
            countIf(is_returning_customer = 0) AS new_orders,
            countIf(is_returning_customer = 1) AS returning_orders,
            sumIf(amount, is_returning_customer = 0) AS new_revenue,
            sumIf(amount, is_returning_customer = 1) AS returning_revenue,
            avgIf(amount, is_returning_customer = 0) AS new_aov,
            avgIf(amount, is_returning_customer = 1) AS returning_aov,
            uniqIf(user_id, is_returning_customer = 0) AS new_customers,
            uniqIf(user_id, is_returning_customer = 1) AS returning_customers
        FROM {table_name}
        WHERE {where}
        """,
        params,
    )[0]
    (
        new_orders,
        returning_orders,
        new_revenue,
        returning_revenue,
        new_aov,
        returning_aov,
        new_customers,
        returning_customers,
    ) = row
    return [
        CustomerSegment(
            segment="New",
            orders=int(new_orders or 0),
            revenue=float(new_revenue or 0),
            avg_order_value=float(new_aov or 0),
            customers=int(new_customers or 0),
        ),
        CustomerSegment(
            segment="Returning",
            orders=int(returning_orders or 0),
            revenue=float(returning_revenue or 0),
            avg_order_value=float(returning_aov or 0),
            customers=int(returning_customers or 0),
        ),
    ]


def get_transactions(
    client,
    table_name: str,
    tenant_id: int,
    owner_user_id: int | None,
    page: int,
    page_size: int,
    sort_by: str,
    sort_dir: str,
    search: str | None = None,
    search_mode: str | None = None,
    filters: dict[str, object] | None = None,
) -> TransactionPage:
    """Fetch a page of transaction rows and total count for pagination."""
    # Offset math must match the UI to avoid gaps and duplicates.
    offset = (page - 1) * page_size
    query, params = build_transactions_query(
        tenant_id,
        owner_user_id,
        sort_by,
        sort_dir,
        table_name,
        filters,
        search,
        search_mode,
    )
    params.update({"limit": page_size, "offset": offset})
    rows = client.execute(query, params)

    count_query, count_params = build_transactions_count_query(
        tenant_id,
        owner_user_id,
        table_name,
        filters,
        search,
        search_mode,
    )
    total = int(client.execute(count_query, count_params)[0][0])

    results = []
    for row in rows:
        record = dict(zip(TRANSACTION_COLUMNS, row))
        record["order_date"] = record["order_date"].isoformat() if record["order_date"] else None
        results.append(TransactionRow(**record))

    return TransactionPage(page=page, page_size=page_size, total=total, rows=results)

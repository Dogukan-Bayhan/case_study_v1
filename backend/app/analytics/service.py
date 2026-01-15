"""Analytics query services."""

from app.analytics.queries import (
    AD_HOC_DIMENSIONS,
    AD_HOC_METRICS,
    TRANSACTION_COLUMNS,
    build_ad_hoc_query,
    build_breakdown_query,
    build_top_products_query,
    build_where_clause,
    build_timeseries_query,
    build_transactions_count_query,
    build_transactions_query,
)
from app.analytics.schemas import (
    AdHocColumn,
    AdHocResponse,
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
    """Aggregate tenant-scoped KPIs for dashboard summary tiles.

    Business purpose:
        Provide headline revenue, order, and customer metrics on the dashboard.
    Why it exists:
        Centralizes KPI aggregation logic with tenant isolation.
    Where used:
        GET /analytics/kpis for the main dashboard.
    Inputs:
        client: ClickHouse client for executing analytics queries.
        table_name: ClickHouse fact table selected by scope.
        tenant_id: Tenant identifier for isolation.
        owner_user_id: Optional owner id for per-user scoping.
        filters: Optional dashboard filters.
    Returns:
        KPIs model with aggregated values.
    """
    # Build tenant/owner scoped WHERE clause before executing aggregates.
    where, params = build_where_clause(tenant_id, owner_user_id, filters)

    # Query computes aggregate revenue, order count, AOV, and unique customers.
    # Written as a single aggregate query to avoid multiple scans.
    # The WHERE clause enforces tenant isolation and optional owner filtering.
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

    # Normalize null aggregates to zero for consistent UI rendering.
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
    """Return time-series data scoped to tenant and optional owner.

    Business purpose:
        Power time-series charts in the analytics dashboard.
    Why it exists:
        Keeps time-series retrieval and formatting in one service layer.
    Where used:
        GET /analytics/timeseries.
    Inputs:
        client: ClickHouse client for executing analytics queries.
        metric: Metric key (revenue, orders, customers).
        grain: Time grain (day, week, month).
        table_name: ClickHouse fact table selected by scope.
        tenant_id: Tenant identifier for isolation.
        owner_user_id: Optional owner id for per-user scoping.
        filters: Optional dashboard filters.
    Returns:
        List of TimeSeriesPoint objects for charting.
    """
    # Build a scoped query using validated metric and grain inputs.
    query, params = build_timeseries_query(metric, grain, tenant_id, owner_user_id, table_name, filters)
    # Query aggregates the requested metric by time bucket for charting.
    # Query builder enforces tenant/owner filters and validated grain selection.
    # Single grouped scan avoids per-series queries and reduces ClickHouse load.
    rows = client.execute(query, params)
    # Normalize numeric values to floats for consistent JSON output.
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
    """Rank products by revenue or quantity with tenant isolation.

    Business purpose:
        Provide leaderboard-style views of top products.
    Why it exists:
        Encapsulates top-product aggregation logic and formatting.
    Where used:
        GET /analytics/top-products.
    Inputs:
        client: ClickHouse client for executing analytics queries.
        table_name: ClickHouse fact table selected by scope.
        tenant_id: Tenant identifier for isolation.
        owner_user_id: Optional owner id for per-user scoping.
        limit: Maximum number of products to return.
        metric: Metric key to rank by.
        filters: Optional dashboard filters.
    Returns:
        List of TopProduct records.
    """
    query, params = build_top_products_query(
        tenant_id,
        owner_user_id,
        table_name,
        limit,
        metric,
        filters,
    )
    # Query ranks products by the selected metric with tenant/owner scoping.
    # Query builder ensures safe ordering and bounded result size.
    # Aggregation and ordering happen in one scan for performance.
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
    """Return aggregated breakdown rows for a given dimension.

    Business purpose:
        Provide rollup tables for dimension-level analytics.
    Why it exists:
        Centralizes breakdown aggregation and formatting logic.
    Where used:
        GET /analytics/breakdown.
    Inputs:
        client: ClickHouse client for executing analytics queries.
        table_name: ClickHouse fact table selected by scope.
        tenant_id: Tenant identifier for isolation.
        owner_user_id: Optional owner id for per-user scoping.
        dimension: Dimension key to group by.
        limit: Maximum rows to return.
        filters: Optional dashboard filters.
    Returns:
        List of BreakdownRow items for the selected dimension.
    """
    query, params = build_breakdown_query(
        dimension,
        tenant_id,
        owner_user_id,
        table_name,
        limit,
        filters,
    )
    # Query computes grouped aggregates for the requested dimension.
    # Query builder applies tenant/owner filters and a hard limit.
    # Single grouped scan avoids additional rollup queries.
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
    """Aggregate metrics for new vs returning customer segments.

    Business purpose:
        Provide customer segmentation metrics for the dashboard.
    Why it exists:
        Encapsulates segment calculations and ensures tenant isolation.
    Where used:
        GET /analytics/customer-segments.
    Inputs:
        client: ClickHouse client for executing analytics queries.
        table_name: ClickHouse fact table selected by scope.
        tenant_id: Tenant identifier for isolation.
        owner_user_id: Optional owner id for per-user scoping.
        filters: Optional dashboard filters.
    Returns:
        List of CustomerSegment entries for New and Returning segments.
    """
    # Build tenant/owner scoped WHERE clause for segment aggregation.
    where, params = build_where_clause(tenant_id, owner_user_id, filters)
    # Query computes counts and revenue for new vs returning customers.
    # Uses conditional aggregates to avoid multiple scans per segment.
    # WHERE clause enforces tenant isolation and optional owner filtering.
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
    """Fetch a page of transaction rows and total count for pagination.

    Business purpose:
        Supply the transactions explorer with paginated transaction rows.
    Why it exists:
        Centralizes pagination math and row formatting.
    Where used:
        GET /analytics/transactions and web transactions page.
    Inputs:
        client: ClickHouse client for executing analytics queries.
        table_name: ClickHouse fact table selected by scope.
        tenant_id: Tenant identifier for isolation.
        owner_user_id: Optional owner id for per-user scoping.
        page: 1-based page index from the UI.
        page_size: Number of rows per page.
        sort_by: Sort column key.
        sort_dir: Sort direction (asc/desc).
        search: Optional transaction_id search term.
        search_mode: "exact" or "contains" matching mode.
        filters: Optional dashboard filters.
    Returns:
        TransactionPage with rows and total count.
    """
    # Offset math must match the UI to avoid gaps and duplicates.
    offset = (page - 1) * page_size
    # Build the row query with strict sort and filter enforcement.
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
    # Query fetches a single page of transaction rows with sorting and filters.
    # Query builder restricts columns and ORDER BY to safe, indexed fields.
    # LIMIT/OFFSET keeps response sizes bounded for UI pagination.
    rows = client.execute(query, params)

    # Count query uses the same filters to compute total rows.
    count_query, count_params = build_transactions_count_query(
        tenant_id,
        owner_user_id,
        table_name,
        filters,
        search,
        search_mode,
    )
    # Query computes the total count for pagination using identical filters.
    # Count query stays narrow to avoid scanning unnecessary columns.
    # Tenant/owner filters maintain isolation and reduce scan scope.
    total = int(client.execute(count_query, count_params)[0][0])

    results = []
    for row in rows:
        # Zip columns to row values for deterministic serialization.
        record = dict(zip(TRANSACTION_COLUMNS, row))
        # Convert dates to ISO strings for JSON responses.
        record["order_date"] = record["order_date"].isoformat() if record["order_date"] else None
        results.append(TransactionRow(**record))

    return TransactionPage(page=page, page_size=page_size, total=total, rows=results)


def get_ad_hoc_results(
    client,
    table_name: str,
    tenant_id: int,
    owner_user_id: int | None,
    scope: str,
    metrics: list[str],
    dimensions: list[str],
    date_grain: str | None,
    filters: dict[str, object] | None,
    limit: int,
    offset: int,
    sort_by: str | None,
    sort_dir: str,
) -> AdHocResponse:
    """Run a grouped ad-hoc analytics query and format the response.

    Business purpose:
        Provide ad-hoc analytics results for the Slice & Dice Studio.
    Why it exists:
        Centralizes query execution, pagination, and response shaping.
    Where used:
        POST /analytics/ad-hoc.
    Inputs:
        client: ClickHouse client for executing analytics queries.
        table_name: ClickHouse fact table selected by scope.
        tenant_id: Tenant identifier for isolation.
        owner_user_id: Optional owner id for per-user scoping.
        scope: Scope label used in the response.
        metrics: Metric keys requested by the UI.
        dimensions: Dimension keys requested by the UI.
        date_grain: Optional date grain for order_date grouping.
        filters: Optional ad-hoc filters.
        limit: Maximum rows per page.
        offset: Offset for pagination.
        sort_by: Sort column requested by the UI.
        sort_dir: Sort direction (asc/desc).
    Returns:
        AdHocResponse containing columns, rows, and pagination metadata.
    """
    # Fetch one extra row to determine if there is a next page.
    query, params, select_keys = build_ad_hoc_query(
        tenant_id,
        owner_user_id,
        table_name,
        metrics,
        dimensions,
        date_grain,
        filters,
        sort_by,
        sort_dir,
        limit + 1,
        offset,
    )
    # Query returns grouped ad-hoc results based on requested metrics/dimensions.
    # Query builder validates columns and uses tenant/owner filters for isolation.
    # Single grouped scan avoids per-metric queries and reduces latency.
    rows = client.execute(query, params)
    # has_more indicates if the caller can request another page.
    has_more = len(rows) > limit
    rows = rows[:limit]

    metric_keys = set(metrics)
    results = []
    for row in rows:
        record = {}
        for key, value in zip(select_keys, row):
            # Normalize date and metric values for consistent JSON output.
            if key == "order_date":
                record[key] = value.isoformat() if value else None
            elif key in metric_keys:
                record[key] = float(value or 0)
            else:
                record[key] = str(value) if value is not None else "Unknown"
        results.append(record)

    columns: list[AdHocColumn] = []
    for dimension in dimensions:
        dim_key = "order_date" if dimension == "order_date" else dimension
        # Dimension metadata drives table and chart rendering in the UI.
        columns.append(
            AdHocColumn(
                key=dim_key,
                label=AD_HOC_DIMENSIONS[dimension]["label"],
                role="dimension",
                format="date" if dimension == "order_date" else "text",
            )
        )
    for metric in metrics:
        # Metric metadata drives formatting and labeling in the UI.
        columns.append(
            AdHocColumn(
                key=metric,
                label=AD_HOC_METRICS[metric]["label"],
                role="metric",
                format=AD_HOC_METRICS[metric]["format"],
            )
        )

    # Default sort is the first metric when requested sort is invalid.
    resolved_sort = sort_by or metrics[0]
    if resolved_sort not in select_keys:
        resolved_sort = metrics[0]

    return AdHocResponse(
        scope=scope,
        metrics=metrics,
        dimensions=dimensions,
        date_grain=date_grain,
        columns=columns,
        rows=results,
        limit=limit,
        offset=offset,
        has_more=has_more,
        sort_by=resolved_sort,
        sort_dir=sort_dir.lower(),
    )

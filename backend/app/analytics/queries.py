"""ClickHouse query builders for analytics."""

from __future__ import annotations

from app.analytics.filters import (
    BOOLEAN_FILTER_FIELDS,
    DATE_FILTER_FIELDS,
    NUMERIC_FILTER_FIELDS,
    STRING_FILTER_FIELDS,
)

ALLOWED_METRICS = {
    "revenue": "sum(amount)",
    "orders": "count()",
    "customers": "uniq(user_id)",
}

AD_HOC_METRICS = {
    "revenue": {"sql": "sum(total_amount)", "label": "Total Revenue", "format": "currency"},
    "orders": {"sql": "count()", "label": "Orders", "format": "number"},
    "avg_order": {"sql": "avg(total_amount)", "label": "Average Order Value", "format": "currency"},
    "avg_discount": {"sql": "avg(discount_percent)", "label": "Average Discount", "format": "percent"},
    "avg_rating": {"sql": "avg(rating)", "label": "Average Rating", "format": "number"},
    "returning_rate": {"sql": "avg(is_returning_customer)", "label": "Returning Customer Rate", "format": "percent"},
}

AD_HOC_DIMENSIONS = {
    "country": {"column": "country", "label": "Country"},
    "city": {"column": "city", "label": "City"},
    "category": {"column": "category", "label": "Category"},
    "department": {"column": "department", "label": "Department"},
    "product_name": {"column": "product_name", "label": "Product Name"},
    "product_code": {"column": "product_code", "label": "Product Code"},
    "payment_method": {"column": "payment_method", "label": "Payment Method"},
    "tier": {"column": "tier", "label": "Tier"},
    "sales_rep_id": {"column": "sales_rep_id", "label": "Sales Rep ID"},
    "region_code": {"column": "region_code", "label": "Region Code"},
    "order_date": {"column": "order_date", "label": "Order Date"},
}

AD_HOC_DATE_GRAINS = {
    "day": "toDate(order_date)",
    "week": "toStartOfWeek(order_date)",
    "month": "toStartOfMonth(order_date)",
}

AD_HOC_STRING_FILTER_FIELDS = {
    "country",
    "city",
    "category",
    "department",
    "payment_method",
    "tier",
}

AD_HOC_BOOLEAN_FILTER_FIELDS = {"is_returning_customer"}

AD_HOC_NUMERIC_FILTER_FIELDS = {
    "rating",
    "quantity",
    "unit_price",
    "discount_percent",
    "tax_rate",
    "total_amount",
}

AD_HOC_DATE_FILTER_FIELDS = {"order_date"}

GRAINS = {
    "day": "toDate(event_ts)",
    "week": "toStartOfWeek(event_ts)",
    "month": "toStartOfMonth(event_ts)",
}

TRANSACTION_COLUMNS = [
    "transaction_id",
    "customer_id",
    "customer_name",
    "email",
    "phone",
    "country",
    "city",
    "postal_code",
    "department",
    "category",
    "product_name",
    "product_code",
    "quantity",
    "unit_price",
    "discount_percent",
    "tax_rate",
    "payment_method",
    "status",
    "tier",
    "order_date",
    "is_returning_customer",
    "loyalty_points",
    "rating",
    "region_code",
    "sales_rep_id",
    "total_amount",
]

TRANSACTION_SORTABLE = {
    "order_date": "order_date",
    "total_amount": "total_amount",
    "quantity": "quantity",
}

BREAKDOWN_DIMENSIONS = {
    "country": "country",
    "category": "category",
    "department": "department",
    "payment_method": "payment_method",
    "tier": "tier",
}

TOP_PRODUCT_METRICS = {
    "revenue": "sum(amount)",
    "quantity": "sum(quantity)",
}


def _apply_filters(where: list[str], params: dict[str, object], filters: dict[str, object] | None) -> None:
    """Apply dashboard-style filters to a WHERE clause builder.

    Business purpose:
        Translate UI filter inputs into safe SQL predicates for analytics queries.
    Why it exists:
        Keeps filter parsing consistent across multiple query builders.
    Where used:
        Used by breakdown, timeseries, and transaction queries.
    Inputs:
        where: List of SQL fragments to append to.
        params: Parameter dict to bind values safely.
        filters: Parsed filter values from request parameters.
    Returns:
        None; mutates where and params in place.
    """
    if not filters:
        return
    # Exact match filters for string dimensions.
    for field in STRING_FILTER_FIELDS:
        value = filters.get(field)
        if not value:
            continue
        key = f"filter_{field}"
        where.append(f"{field} = %({key})s")
        params[key] = value

    # Boolean filters map to 0/1 flags in ClickHouse.
    for field in BOOLEAN_FILTER_FIELDS:
        if field not in filters:
            continue
        key = f"filter_{field}"
        where.append(f"{field} = %({key})s")
        params[key] = filters[field]

    # Numeric range filters support min/max bounds.
    for field in NUMERIC_FILTER_FIELDS:
        min_key = f"{field}_min"
        max_key = f"{field}_max"
        if min_key in filters:
            where.append(f"{field} >= %({min_key})s")
            params[min_key] = filters[min_key]
        if max_key in filters:
            where.append(f"{field} <= %({max_key})s")
            params[max_key] = filters[max_key]

    # Date range filters are parsed using ClickHouse best-effort parsing.
    for field in DATE_FILTER_FIELDS:
        start_key = f"{field}_start"
        end_key = f"{field}_end"
        if start_key in filters:
            where.append(f"{field} >= parseDateTimeBestEffort(%({start_key})s)")
            params[start_key] = filters[start_key]
        if end_key in filters:
            where.append(f"{field} <= parseDateTimeBestEffort(%({end_key})s)")
            params[end_key] = filters[end_key]


def _build_where_clause(
    tenant_id: int,
    owner_user_id: int | None,
    filters: dict[str, object] | None,
) -> tuple[str, dict[str, object]]:
    """Assemble a tenant-scoped WHERE clause with optional filters.

    Business purpose:
        Enforce tenant isolation and optional owner scoping in analytics queries.
    Why it exists:
        Centralizes mandatory filters to avoid accidental cross-tenant access.
    Where used:
        Shared by KPI, breakdown, timeseries, and transaction queries.
    Inputs:
        tenant_id: Tenant identifier to scope data access.
        owner_user_id: Optional user id for per-user isolation.
        filters: Additional request filters to append.
    Returns:
        Tuple of SQL WHERE clause string and bound parameters dict.
    """
    # Tenant scope is always required for isolation.
    where = ["tenant_id = %(tenant_id)s"]
    params: dict[str, object] = {"tenant_id": tenant_id}
    if owner_user_id is not None:
        # Owner scoping is used for NORMAL users only.
        where.append("owner_user_id = %(owner_user_id)s")
        params["owner_user_id"] = owner_user_id
    # Apply additional dashboard filters if provided.
    _apply_filters(where, params, filters)
    return " AND ".join(where), params


def build_where_clause(
    tenant_id: int,
    owner_user_id: int | None,
    filters: dict[str, object] | None,
) -> tuple[str, dict[str, object]]:
    """Public wrapper to build a scoped WHERE clause for analytics queries.

    Business purpose:
        Provide a stable API for modules that need query predicates.
    Why it exists:
        Shields callers from internal helper naming and behavior changes.
    Where used:
        Called by analytics service functions and query builders.
    Inputs:
        tenant_id: Tenant identifier for isolation.
        owner_user_id: Optional user id for per-user scoping.
        filters: Optional filter map from request params.
    Returns:
        Tuple of WHERE clause string and query parameters.
    """
    return _build_where_clause(tenant_id, owner_user_id, filters)


def _apply_transaction_search(
    where: list[str],
    params: dict[str, object],
    search: str | None,
    search_mode: str | None,
) -> None:
    """Apply transaction_id search filters to a WHERE clause builder.

    Business purpose:
        Support transaction lookups and fuzzy search in the transactions table.
    Why it exists:
        Encapsulates exact vs contains search behavior for consistency.
    Where used:
        Transaction query and count query builders.
    Inputs:
        where: List of SQL predicates to mutate.
        params: Parameter dict to bind search input safely.
        search: User-provided search string.
        search_mode: "exact" or "contains" matching behavior.
    Returns:
        None; mutates where and params in place.
    """
    if not search:
        return
    # Use a consistent parameter name for both exact and partial search modes.
    key = "search"
    params[key] = search
    if search_mode == "exact":
        # Exact match avoids substring scan when user wants exact id.
        where.append(f"transaction_id = %({key})s")
        return
    # positionCaseInsensitive enables fast substring search on transaction_id.
    where.append(f"positionCaseInsensitive(transaction_id, %({key})s) > 0")


def _coerce_bool_value(value: object) -> int | None:
    """Normalize loosely typed boolean inputs to ClickHouse-compatible integers.

    Business purpose:
        Accept UI inputs and coerce them to 0/1 flags.
    Why it exists:
        ClickHouse booleans are stored as UInt8, not Python bools.
    Where used:
        Ad-hoc filter parsing for is_returning_customer.
    Inputs:
        value: Raw input value from request payloads.
    Returns:
        1 or 0 when coercible, otherwise None.
    """
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return 1
        if normalized in {"0", "false", "no"}:
            return 0
    return None


def _coerce_float_value(value: object) -> float | None:
    """Convert input values to floats when possible.

    Business purpose:
        Normalize numeric filter inputs for ClickHouse queries.
    Why it exists:
        Request payloads may send numbers as strings or None.
    Where used:
        Ad-hoc numeric filter parsing.
    Inputs:
        value: Raw input value.
    Returns:
        Float if conversion succeeds, otherwise None.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _apply_ad_hoc_filters(
    prewhere: list[str],
    where: list[str],
    params: dict[str, object],
    filters: dict[str, object] | None,
) -> None:
    """Apply ad-hoc filters with PREWHERE optimization for ClickHouse.

    Business purpose:
        Translate ad-hoc filter payloads into ClickHouse predicates.
    Why it exists:
        Ad-hoc analytics uses a different payload shape and needs PREWHERE usage.
    Where used:
        Ad-hoc query builder for the Slice & Dice Studio endpoint.
    Inputs:
        prewhere: List of PREWHERE predicates (tenant/date for pruning).
        where: List of WHERE predicates for remaining filters.
        params: Bound parameters dict to prevent SQL injection.
        filters: Ad-hoc filter payload from the request body.
    Returns:
        None; mutates prewhere, where, and params in place.
    """
    if not filters:
        return

    # Apply string filters as exact matches or IN lists.
    for field in AD_HOC_STRING_FILTER_FIELDS:
        value = filters.get(field)
        if value is None:
            continue
        key = f"filter_{field}"
        if isinstance(value, (list, tuple)):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            if cleaned:
                where.append(f"{field} IN %({key})s")
                params[key] = cleaned
            continue
        value_str = str(value).strip()
        if value_str:
            where.append(f"{field} = %({key})s")
            params[key] = value_str

    # Normalize booleans to 0/1 to match ClickHouse storage.
    for field in AD_HOC_BOOLEAN_FILTER_FIELDS:
        if field not in filters:
            continue
        value = _coerce_bool_value(filters.get(field))
        if value is None:
            continue
        key = f"filter_{field}"
        where.append(f"{field} = %({key})s")
        params[key] = value

    # Support range and exact matches for numeric metrics.
    for field in AD_HOC_NUMERIC_FILTER_FIELDS:
        value = filters.get(field)
        if value is None:
            continue
        min_key = f"{field}_min"
        max_key = f"{field}_max"
        if isinstance(value, dict):
            lower = _coerce_float_value(value.get("gte") if value.get("gte") is not None else value.get("min"))
            upper = _coerce_float_value(value.get("lte") if value.get("lte") is not None else value.get("max"))
            if lower is not None:
                where.append(f"{field} >= %({min_key})s")
                params[min_key] = lower
            if upper is not None:
                where.append(f"{field} <= %({max_key})s")
                params[max_key] = upper
            continue
        exact = _coerce_float_value(value)
        if exact is not None:
            where.append(f"{field} = %({min_key})s")
            params[min_key] = exact

    # Date filters go into PREWHERE to enable ClickHouse pruning.
    for field in AD_HOC_DATE_FILTER_FIELDS:
        value = filters.get(field)
        if not isinstance(value, dict):
            continue
        start_value = value.get("from") if value.get("from") is not None else value.get("start")
        end_value = value.get("to") if value.get("to") is not None else value.get("end")
        if start_value:
            key = f"{field}_start"
            prewhere.append(f"{field} >= parseDateTimeBestEffort(%({key})s)")
            params[key] = start_value
        if end_value:
            key = f"{field}_end"
            prewhere.append(f"{field} <= parseDateTimeBestEffort(%({key})s)")
            params[key] = end_value


def build_ad_hoc_query(
    tenant_id: int,
    owner_user_id: int | None,
    table_name: str,
    metrics: list[str],
    dimensions: list[str],
    date_grain: str | None,
    filters: dict[str, object] | None,
    sort_by: str | None,
    sort_dir: str,
    limit: int,
    offset: int,
) -> tuple[str, dict[str, object], list[str]]:
    """Build a single grouped query for ad-hoc analytics results.

    Business purpose:
        Power the Slice & Dice Studio with one fast, grouped query per request.
    Why it exists:
        Ensures consistent metric/dimension validation and query safety.
    Where used:
        Analytics service for POST /analytics/ad-hoc.
    Inputs:
        tenant_id: Tenant identifier for data isolation.
        owner_user_id: Optional owner id for per-user scoping.
        table_name: ClickHouse fact table selected by scope.
        metrics: Metric keys requested by the UI.
        dimensions: Dimension keys requested by the UI.
        date_grain: Optional grain for order_date grouping.
        filters: Ad-hoc filter payload map.
        sort_by: Requested sort column (metric or dimension).
        sort_dir: Sort direction (asc/desc).
        limit: Row limit for pagination.
        offset: Offset for pagination.
    Returns:
        Tuple of query string, bound parameters, and select column keys.
    """
    if not metrics or not dimensions:
        raise ValueError("At least one metric and one dimension are required")
    if len(dimensions) > 3:
        raise ValueError("A maximum of three dimensions is supported")

    for metric in metrics:
        if metric not in AD_HOC_METRICS:
            raise ValueError(f"Unsupported metric: {metric}")

    for dimension in dimensions:
        if dimension not in AD_HOC_DIMENSIONS:
            raise ValueError(f"Unsupported dimension: {dimension}")

    grain = date_grain or "day"
    if grain not in AD_HOC_DATE_GRAINS:
        raise ValueError("Unsupported date grain")

    select_parts: list[str] = []
    group_by: list[str] = []
    select_keys: list[str] = []

    # Map selected dimensions to explicit columns and optional date transforms.
    for dimension in dimensions:
        if dimension == "order_date":
            select_parts.append(f"{AD_HOC_DATE_GRAINS[grain]} AS order_date")
            group_by.append("order_date")
            select_keys.append("order_date")
        else:
            column = AD_HOC_DIMENSIONS[dimension]["column"]
            select_parts.append(f"{column} AS {dimension}")
            group_by.append(dimension)
            select_keys.append(dimension)

    # Append metric aggregations to the SELECT list.
    for metric in metrics:
        select_parts.append(f"{AD_HOC_METRICS[metric]['sql']} AS {metric}")
        select_keys.append(metric)

    # PREWHERE is used for tenant/owner and date pruning.
    prewhere = ["tenant_id = %(tenant_id)s"]
    params: dict[str, object] = {"tenant_id": tenant_id}
    if owner_user_id is not None:
        prewhere.append("owner_user_id = %(owner_user_id)s")
        params["owner_user_id"] = owner_user_id

    where: list[str] = []
    _apply_ad_hoc_filters(prewhere, where, params, filters)

    # Prevent null buckets in time series dimension groupings.
    if "order_date" in dimensions:
        where.append("order_date IS NOT NULL")

    # Constrain ordering to selected columns for safety.
    sort_direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
    resolved_sort = sort_by or metrics[0]
    if resolved_sort not in select_keys:
        resolved_sort = metrics[0]
    params["limit"] = limit
    params["offset"] = offset

    # Query computes grouped metrics for selected dimensions in one scan.
    # Written as a single SELECT/GROUP BY to avoid multiple table scans.
    # PREWHERE is used to prune on tenant/owner/date for performance.
    query = (
        "SELECT "
        f"{', '.join(select_parts)} "
        f"FROM {table_name} "
        f"PREWHERE {' AND '.join(prewhere)} "
        f"WHERE {' AND '.join(where) if where else '1'} "
        f"GROUP BY {', '.join(group_by)} "
        f"ORDER BY {resolved_sort} {sort_direction} "
        "LIMIT %(limit)s OFFSET %(offset)s"
    )
    return query, params, select_keys


def build_timeseries_query(
    metric: str,
    grain: str,
    tenant_id: int,
    owner_user_id: int | None,
    table_name: str,
    filters: dict[str, object] | None = None,
) -> tuple[str, dict[str, object]]:
    """Build a tenant-scoped time series query for charting.

    Business purpose:
        Provide time series data for dashboard charts.
    Why it exists:
        Encapsulates allowed metrics and time grains for consistent queries.
    Where used:
        Analytics service for GET /analytics/timeseries.
    Inputs:
        metric: Allowed metric key (revenue, orders, customers).
        grain: Time grain (day, week, month).
        tenant_id: Tenant identifier for isolation.
        owner_user_id: Optional per-user filter.
        table_name: ClickHouse fact table to query.
        filters: Optional dashboard filters.
    Returns:
        Tuple of SQL query string and bound parameters.
    """
    if metric not in ALLOWED_METRICS:
        raise ValueError("Unsupported metric")
    if grain not in GRAINS:
        raise ValueError("Unsupported grain")

    # Build tenant/owner WHERE clause first to enforce isolation.
    where, params = _build_where_clause(tenant_id, owner_user_id, filters)

    # Query computes aggregated metric values grouped by time bucket.
    # Written with a single GROUP BY on the bucket for chart rendering.
    # Tenant/owner filters are pushed into WHERE for isolation.
    query = (
        "SELECT "
        f"{GRAINS[grain]} AS bucket, {ALLOWED_METRICS[metric]} AS value "
        f"FROM {table_name} "
        f"WHERE {where} "
        "GROUP BY bucket "
        "ORDER BY bucket"
    )
    return query, params


def build_transactions_query(
    tenant_id: int,
    owner_user_id: int | None,
    sort_by: str,
    sort_dir: str,
    table_name: str,
    filters: dict[str, object] | None = None,
    search: str | None = None,
    search_mode: str | None = None,
) -> tuple[str, dict[str, object]]:
    """Build the paginated transactions query for the transactions table.

    Business purpose:
        Supply transaction-level rows to the UI with server-side pagination.
    Why it exists:
        Ensures sort and filter behavior is validated and safe.
    Where used:
        Analytics service for GET /analytics/transactions.
    Inputs:
        tenant_id: Tenant identifier for isolation.
        owner_user_id: Optional per-user filter for NORMAL users.
        sort_by: Column requested for sorting.
        sort_dir: Sort direction (asc/desc).
        table_name: ClickHouse fact table to query.
        filters: Optional dashboard filters.
        search: Optional transaction_id search term.
        search_mode: "exact" or "contains" matching mode.
    Returns:
        Tuple of SQL query string and bound parameters.
    """
    if sort_by not in TRANSACTION_SORTABLE:
        raise ValueError("Unsupported sort column")
    
    # Enforce sort direction and build scope filters.
    sort_direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
    where_clause, params = _build_where_clause(tenant_id, owner_user_id, filters)
    where_parts = [where_clause]
    _apply_transaction_search(where_parts, params, search, search_mode)

    # Only select known transaction columns to avoid exposing extra data.
    columns = ", ".join(TRANSACTION_COLUMNS)
    sort_column = TRANSACTION_SORTABLE[sort_by]
    # Query returns a single page of transactions with deterministic ordering.
    # Written with an explicit column list to avoid leaking extra fields.
    # LIMIT/OFFSET supports server-side pagination.
    query = (
        f"SELECT {columns} "
        f"FROM {table_name} "
        f"WHERE {' AND '.join(where_parts)} "
        f"ORDER BY {sort_column} {sort_direction} "
        "LIMIT %(limit)s OFFSET %(offset)s"
    )
    return query, params


def build_transactions_count_query(
    tenant_id: int,
    owner_user_id: int | None,
    table_name: str,
    filters: dict[str, object] | None = None,
    search: str | None = None,
    search_mode: str | None = None,
) -> tuple[str, dict[str, object]]:
    """Build the count query that backs server-side pagination totals.

    Business purpose:
        Provide total row counts for the transactions table pager.
    Why it exists:
        Separates count logic from row-fetch logic for correctness.
    Where used:
        Analytics service when building TransactionPage metadata.
    Inputs:
        tenant_id: Tenant identifier for isolation.
        owner_user_id: Optional per-user filter.
        table_name: ClickHouse fact table to query.
        filters: Optional dashboard filters.
        search: Optional transaction_id search term.
        search_mode: "exact" or "contains" matching mode.
    Returns:
        Tuple of SQL query string and bound parameters.
    """
    # Reuse the same WHERE predicate and search logic as the row query.
    where_clause, params = _build_where_clause(tenant_id, owner_user_id, filters)
    where_parts = [where_clause]
    _apply_transaction_search(where_parts, params, search, search_mode)
    # Query computes total count for pagination UI.
    # Written as a lightweight count() with the same filters for accuracy.
    return f"SELECT count() FROM {table_name} WHERE {' AND '.join(where_parts)}", params


def build_breakdown_query(
    dimension: str,
    tenant_id: int,
    owner_user_id: int | None,
    table_name: str,
    limit: int,
    filters: dict[str, object] | None,
) -> tuple[str, dict[str, object]]:
    """Build a grouped breakdown query for dashboard rollups.

    Business purpose:
        Provide grouped revenue/volume breakdowns by a single dimension.
    Why it exists:
        Keeps aggregation logic consistent and validated per dimension.
    Where used:
        Analytics service for GET /analytics/breakdown.
    Inputs:
        dimension: Supported dimension key to group by.
        tenant_id: Tenant identifier for isolation.
        owner_user_id: Optional per-user filter.
        table_name: ClickHouse fact table to query.
        limit: Maximum rows to return.
        filters: Optional dashboard filters.
    Returns:
        Tuple of SQL query string and bound parameters.
    """
    if dimension not in BREAKDOWN_DIMENSIONS:
        raise ValueError("Unsupported breakdown dimension")
    # Ensure tenant/owner isolation before grouping.
    where, params = _build_where_clause(tenant_id, owner_user_id, filters)
    params["limit"] = limit
    column = BREAKDOWN_DIMENSIONS[dimension]
    # Query aggregates revenue, orders, AOV, and quantity for the dimension.
    # Written as a single GROUP BY to avoid repeated scans.
    # LIMIT caps output to keep UI and query costs bounded.
    query = (
        "SELECT "
        f"{column} AS key, "
        "sum(amount) AS revenue, "
        "count() AS orders, "
        "avg(amount) AS avg_order_value, "
        "sum(quantity) AS quantity "
        f"FROM {table_name} "
        f"WHERE {where} "
        "GROUP BY key "
        "ORDER BY revenue DESC "
        "LIMIT %(limit)s"
    )
    return query, params


def build_top_products_query(
    tenant_id: int,
    owner_user_id: int | None,
    table_name: str,
    limit: int,
    metric: str,
    filters: dict[str, object] | None,
) -> tuple[str, dict[str, object]]:
    """Build a leaderboard query for top products.

    Business purpose:
        Rank products by revenue or quantity for dashboard leaderboards.
    Why it exists:
        Standardizes metric selection and tenant scoping for top lists.
    Where used:
        Analytics service for GET /analytics/top-products.
    Inputs:
        tenant_id: Tenant identifier for isolation.
        owner_user_id: Optional per-user filter.
        table_name: ClickHouse fact table to query.
        limit: Maximum rows to return.
        metric: Metric key (revenue or quantity).
        filters: Optional dashboard filters.
    Returns:
        Tuple of SQL query string and bound parameters.
    """
    if metric not in TOP_PRODUCT_METRICS:
        raise ValueError("Unsupported top product metric")
    # Build scoped WHERE clause prior to aggregation.
    where, params = _build_where_clause(tenant_id, owner_user_id, filters)
    params["limit"] = limit
    # Query aggregates products with fallback naming and sorts by metric.
    # Written with coalesce to ensure stable grouping keys.
    # LIMIT keeps leaderboard queries fast.
    query = (
        "SELECT "
        "coalesce(product_name, product_code, product_id, 'Unknown') AS product, "
        f"{TOP_PRODUCT_METRICS[metric]} AS value "
        f"FROM {table_name} "
        f"WHERE {where} "
        "GROUP BY product "
        "ORDER BY value DESC "
        "LIMIT %(limit)s"
    )
    return query, params

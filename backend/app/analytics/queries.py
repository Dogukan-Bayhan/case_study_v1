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
    if not filters:
        return
    for field in STRING_FILTER_FIELDS:
        value = filters.get(field)
        if not value:
            continue
        key = f"filter_{field}"
        where.append(f"{field} = %({key})s")
        params[key] = value

    for field in BOOLEAN_FILTER_FIELDS:
        if field not in filters:
            continue
        key = f"filter_{field}"
        where.append(f"{field} = %({key})s")
        params[key] = filters[field]

    for field in NUMERIC_FILTER_FIELDS:
        min_key = f"{field}_min"
        max_key = f"{field}_max"
        if min_key in filters:
            where.append(f"{field} >= %({min_key})s")
            params[min_key] = filters[min_key]
        if max_key in filters:
            where.append(f"{field} <= %({max_key})s")
            params[max_key] = filters[max_key]

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
    where = ["tenant_id = %(tenant_id)s"]
    params: dict[str, object] = {"tenant_id": tenant_id}
    if owner_user_id is not None:
        where.append("owner_user_id = %(owner_user_id)s")
        params["owner_user_id"] = owner_user_id
    _apply_filters(where, params, filters)
    return " AND ".join(where), params


def build_where_clause(
    tenant_id: int,
    owner_user_id: int | None,
    filters: dict[str, object] | None,
) -> tuple[str, dict[str, object]]:
    """Public wrapper to assemble WHERE clauses for analytics queries."""
    return _build_where_clause(tenant_id, owner_user_id, filters)


def _apply_transaction_search(
    where: list[str],
    params: dict[str, object],
    search: str | None,
    search_mode: str | None,
) -> None:
    if not search:
        return
    key = "search"
    params[key] = search
    if search_mode == "exact":
        where.append(f"transaction_id = %({key})s")
        return
    where.append(f"positionCaseInsensitive(transaction_id, %({key})s) > 0")


def build_timeseries_query(
    metric: str,
    grain: str,
    tenant_id: int,
    owner_user_id: int | None,
    table_name: str,
    filters: dict[str, object] | None = None,
) -> tuple[str, dict[str, object]]:
    
    """Build a tenant-scoped time series query with optional owner filtering."""
    if metric not in ALLOWED_METRICS:
        raise ValueError("Unsupported metric")
    if grain not in GRAINS:
        raise ValueError("Unsupported grain")

    where, params = _build_where_clause(tenant_id, owner_user_id, filters)

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
    
    """Build a paginated transactions query with enforced sort whitelist."""
    if sort_by not in TRANSACTION_SORTABLE:
        raise ValueError("Unsupported sort column")
    
    sort_direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
    where_clause, params = _build_where_clause(tenant_id, owner_user_id, filters)
    where_parts = [where_clause]
    _apply_transaction_search(where_parts, params, search, search_mode)

    columns = ", ".join(TRANSACTION_COLUMNS)
    sort_column = TRANSACTION_SORTABLE[sort_by]
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
    """Build the matching count query used for server-side pagination."""
    where_clause, params = _build_where_clause(tenant_id, owner_user_id, filters)
    where_parts = [where_clause]
    _apply_transaction_search(where_parts, params, search, search_mode)
    return f"SELECT count() FROM {table_name} WHERE {' AND '.join(where_parts)}", params


def build_breakdown_query(
    dimension: str,
    tenant_id: int,
    owner_user_id: int | None,
    table_name: str,
    limit: int,
    filters: dict[str, object] | None,
) -> tuple[str, dict[str, object]]:
    """Build a grouped breakdown query for a supported dimension."""
    if dimension not in BREAKDOWN_DIMENSIONS:
        raise ValueError("Unsupported breakdown dimension")
    where, params = _build_where_clause(tenant_id, owner_user_id, filters)
    params["limit"] = limit
    column = BREAKDOWN_DIMENSIONS[dimension]
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
    """Build a product leaderboard query by revenue or quantity."""
    if metric not in TOP_PRODUCT_METRICS:
        raise ValueError("Unsupported top product metric")
    where, params = _build_where_clause(tenant_id, owner_user_id, filters)
    params["limit"] = limit
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

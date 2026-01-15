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


def _coerce_bool_value(value: object) -> int | None:
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
    if not filters:
        return

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

    for field in AD_HOC_BOOLEAN_FILTER_FIELDS:
        if field not in filters:
            continue
        value = _coerce_bool_value(filters.get(field))
        if value is None:
            continue
        key = f"filter_{field}"
        where.append(f"{field} = %({key})s")
        params[key] = value

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
    """Build a single grouped query for ad-hoc analytics."""
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

    for metric in metrics:
        select_parts.append(f"{AD_HOC_METRICS[metric]['sql']} AS {metric}")
        select_keys.append(metric)

    prewhere = ["tenant_id = %(tenant_id)s"]
    params: dict[str, object] = {"tenant_id": tenant_id}
    if owner_user_id is not None:
        prewhere.append("owner_user_id = %(owner_user_id)s")
        params["owner_user_id"] = owner_user_id

    where: list[str] = []
    _apply_ad_hoc_filters(prewhere, where, params, filters)

    if "order_date" in dimensions:
        where.append("order_date IS NOT NULL")

    sort_direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
    resolved_sort = sort_by or metrics[0]
    if resolved_sort not in select_keys:
        resolved_sort = metrics[0]
    params["limit"] = limit
    params["offset"] = offset

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

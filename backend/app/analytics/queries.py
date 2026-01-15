"""ClickHouse query builders for analytics."""

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


def build_timeseries_query(
    metric: str,
    grain: str,
    tenant_id: int,
    owner_user_id: int | None,
    table_name: str,
) -> str:
    
    """Build a tenant-scoped time series query with optional owner filtering."""
    if metric not in ALLOWED_METRICS:
        raise ValueError("Unsupported metric")
    if grain not in GRAINS:
        raise ValueError("Unsupported grain")

    where = f"tenant_id = {tenant_id}"
    if owner_user_id is not None:
        where += f" AND owner_user_id = {owner_user_id}"

    return (
        "SELECT "
        f"{GRAINS[grain]} AS bucket, {ALLOWED_METRICS[metric]} AS value "
        f"FROM {table_name} "
        f"WHERE {where} "
        "GROUP BY bucket "
        "ORDER BY bucket"
    )


def build_transactions_query(
    tenant_id: int,
    owner_user_id: int | None,
    sort_by: str,
    sort_dir: str,
    table_name: str,
) -> str:
    
    """Build a paginated transactions query with enforced sort whitelist."""
    if sort_by not in TRANSACTION_SORTABLE:
        raise ValueError("Unsupported sort column")
    
    sort_direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
    where = "tenant_id = %(tenant_id)s"
    if owner_user_id is not None:
        where += " AND owner_user_id = %(owner_user_id)s"

    columns = ", ".join(TRANSACTION_COLUMNS)
    sort_column = TRANSACTION_SORTABLE[sort_by]
    return (
        f"SELECT {columns} "
        f"FROM {table_name} "
        f"WHERE {where} "
        f"ORDER BY {sort_column} {sort_direction} "
        "LIMIT %(limit)s OFFSET %(offset)s"
    )


def build_transactions_count_query(
    tenant_id: int,
    owner_user_id: int | None,
    table_name: str,
) -> str:
    """Build the matching count query used for server-side pagination."""
    where = "tenant_id = %(tenant_id)s"
    if owner_user_id is not None:
        where += " AND owner_user_id = %(owner_user_id)s"
    return f"SELECT count() FROM {table_name} WHERE {where}"

"""
Analytics API routes.

This module exposes analytics-related HTTP endpoints under the `/analytics` prefix.

Responsibilities of this layer:
- Receive HTTP requests from the frontend
- Enforce authentication and authorization
- Apply tenant and role-based data scoping
- Validate query parameters
- Delegate heavy analytics logic to the service layer
- Return structured, validated responses to the UI
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

# Whitelist of allowed columns that can be used in ORDER BY clauses
# This prevents SQL injection and unsupported sorting
from app.analytics.queries import TRANSACTION_SORTABLE, build_where_clause
from app.analytics.filters import FILTER_OPTION_FIELDS, parse_filters
from app.analytics.schemas import (
    BreakdownRow,
    CustomerSegment,
    FilterOption,
    KPIs,
    TimeSeriesPoint,
    TopProduct,
    TransactionPage,
)
from app.analytics.service import (
    get_breakdown,
    get_customer_segments,
    get_kpis,
    get_timeseries,
    get_top_products,
    get_transactions,
)
from app.analytics.tables import SCOPE_VALUES, build_scope_table
from app.core.deps import get_clickhouse, get_current_user, get_settings
from app.db.models import RoleEnum, User

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _resolve_scope(scope: str, current_user: User) -> str:
    normalized = scope.lower()
    if normalized not in SCOPE_VALUES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported scope")
    if current_user.role == RoleEnum.NORMAL and normalized != "clean":
        return "clean"
    return normalized


def _parse_filters(request: Request) -> dict[str, object]:
    return parse_filters(request.query_params)

# ---------------------------------------------------------------------
# KPI ENDPOINT
# ---------------------------------------------------------------------
@router.get("/kpis", response_model=KPIs)
def kpis(
    # The currently authenticated user (resolved from JWT / cookie)
    scope: str = Query("clean"),
    current_user: User = Depends(get_current_user),
    # ClickHouse client used for analytics queries
    client=Depends(get_clickhouse),
    # Application settings (used to resolve database/schema names)
    settings=Depends(get_settings),
    filters: dict[str, object] = Depends(_parse_filters),
) -> KPIs:
    """
    GET /analytics/kpis

    Frontend request:
        GET /analytics/kpis
        Authorization: Bearer <JWT>

    No query parameters are required.

    Purpose:
    - Return high-level KPI aggregates for the dashboard tiles
    - Metrics include revenue, order count, average order value, and unique customers

    Authorization & scoping:
    - ADMIN / GUEST users see all data within their tenant
    - NORMAL users see only their own transactions
    """

    # NORMAL users are restricted to their own data
    # ADMIN and GUEST users can see all data for the tenant
    owner_filter = None if current_user.role != RoleEnum.NORMAL else current_user.id
    # Resolve the analytics fact table in ClickHouse
    table = build_scope_table(settings, _resolve_scope(scope, current_user))
    # Delegate KPI aggregation to the analytics service layer
    return get_kpis(client, table, current_user.tenant_id, owner_filter, filters)


# ---------------------------------------------------------------------
# TIME SERIES ENDPOINT (Charts)
# ---------------------------------------------------------------------
@router.get("/timeseries", response_model=list[TimeSeriesPoint])
def timeseries(

    # Metric to aggregate (revenue, orders, customers)
    metric: str = Query("revenue"),

    # Time grain for grouping (day, week, month)
    grain: str = Query("day"),
    scope: str = Query("clean"),
    current_user: User = Depends(get_current_user),
    client=Depends(get_clickhouse),
    settings=Depends(get_settings),
    filters: dict[str, object] = Depends(_parse_filters),
) -> list[TimeSeriesPoint]:
    """
    GET /analytics/timeseries

    Frontend request examples:
        GET /analytics/timeseries?metric=revenue&grain=day
        GET /analytics/timeseries?metric=orders&grain=month

    Purpose:
    - Provide time-series data for charts (line / bar charts)
    - Aggregates data over time based on the selected metric and grain

    Authorization & scoping:
    - ADMIN / GUEST users see tenant-wide time series
    - NORMAL users see only their own activity
    """

    # Apply owner-level filtering only for NORMAL users
    owner_filter = None if current_user.role != RoleEnum.NORMAL else current_user.id
    table = build_scope_table(settings, _resolve_scope(scope, current_user))
    return get_timeseries(client, metric, grain, table, current_user.tenant_id, owner_filter, filters)


# ---------------------------------------------------------------------
# TOP PRODUCTS ENDPOINT
# ---------------------------------------------------------------------
@router.get("/top-products", response_model=list[TopProduct])
def top_products(
    # Maximum number of products to return (bounded for safety)
    limit: int = Query(10, ge=1, le=100),
    metric: str = Query("revenue"),
    scope: str = Query("clean"),

    current_user: User = Depends(get_current_user),
    client=Depends(get_clickhouse),
    settings=Depends(get_settings),
    filters: dict[str, object] = Depends(_parse_filters),
) -> list[TopProduct]:
    """
    GET /analytics/top-products

    Frontend request example:
        GET /analytics/top-products?limit=10

    Purpose:
    - Return a ranked list of top products by revenue
    - Used for leaderboard-style analytics or summary tables

    Authorization & scoping:
    - ADMIN / GUEST users see tenant-wide rankings
    - NORMAL users see rankings based only on their own transactions
    """
    owner_filter = None if current_user.role != RoleEnum.NORMAL else current_user.id
    table = build_scope_table(settings, _resolve_scope(scope, current_user))
    return get_top_products(client, table, current_user.tenant_id, owner_filter, limit, metric, filters)


# ---------------------------------------------------------------------
# BREAKDOWN ENDPOINT (aggregated rollups)
# ---------------------------------------------------------------------
@router.get("/breakdown", response_model=list[BreakdownRow])
def breakdown(
    dimension: str = Query(...),
    limit: int = Query(12, ge=1, le=50),
    scope: str = Query("clean"),
    current_user: User = Depends(get_current_user),
    client=Depends(get_clickhouse),
    settings=Depends(get_settings),
    filters: dict[str, object] = Depends(_parse_filters),
) -> list[BreakdownRow]:
    owner_filter = None if current_user.role != RoleEnum.NORMAL else current_user.id
    table = build_scope_table(settings, _resolve_scope(scope, current_user))
    return get_breakdown(client, table, current_user.tenant_id, owner_filter, dimension, limit, filters)


# ---------------------------------------------------------------------
# CUSTOMER SEGMENTS ENDPOINT
# ---------------------------------------------------------------------
@router.get("/customer-segments", response_model=list[CustomerSegment])
def customer_segments(
    scope: str = Query("clean"),
    current_user: User = Depends(get_current_user),
    client=Depends(get_clickhouse),
    settings=Depends(get_settings),
    filters: dict[str, object] = Depends(_parse_filters),
) -> list[CustomerSegment]:
    owner_filter = None if current_user.role != RoleEnum.NORMAL else current_user.id
    table = build_scope_table(settings, _resolve_scope(scope, current_user))
    return get_customer_segments(client, table, current_user.tenant_id, owner_filter, filters)


# ---------------------------------------------------------------------
# FILTER OPTIONS ENDPOINT
# ---------------------------------------------------------------------
@router.get("/filter-options", response_model=list[FilterOption])
def filter_options(
    field: str = Query(...),
    q: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    scope: str = Query("clean"),
    current_user: User = Depends(get_current_user),
    client=Depends(get_clickhouse),
    settings=Depends(get_settings),
    filters: dict[str, object] = Depends(_parse_filters),
) -> list[FilterOption]:
    if field not in FILTER_OPTION_FIELDS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported filter field")
    if field in filters:
        filters.pop(field)
    if q:
        q = q.strip() or None
    owner_filter = None if current_user.role != RoleEnum.NORMAL else current_user.id
    table = build_scope_table(settings, _resolve_scope(scope, current_user))
    where, params = build_where_clause(current_user.tenant_id, owner_filter, filters)
    params["limit"] = limit
    if q:
        params["search"] = q
        where += f" AND positionCaseInsensitive({field}, %(search)s) > 0"
    query = (
        f"SELECT DISTINCT {field} AS value "
        f"FROM {table} "
        f"WHERE {where} AND {field} IS NOT NULL AND {field} != '' "
        "ORDER BY value "
        "LIMIT %(limit)s"
    )
    rows = client.execute(query, params)
    return [FilterOption(value=str(row[0])) for row in rows]


# ---------------------------------------------------------------------
# TRANSACTIONS ENDPOINT (Paginated Table)
# ---------------------------------------------------------------------
@router.get("/transactions", response_model=TransactionPage)
def transactions(
    # Page number (1-based indexing)
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    pageSize: int | None = Query(None, ge=1, le=100),
    sort_by: str = Query("order_date"),
    sort_dir: str = Query("desc"),
    search: str | None = Query(None),
    search_mode: str = Query("contains"),
    current_user: User = Depends(get_current_user),
    client=Depends(get_clickhouse),
    settings=Depends(get_settings),
) -> TransactionPage:
    """
    GET /analytics/transactions

    Frontend request example:
        GET /analytics/transactions?page=1&pageSize=25&sort_by=order_date&sort_dir=desc

    Purpose:
    - Return a paginated list of individual transactions
    - Used to populate the main transactions table in the UI

    Features:
    - Server-side pagination
    - Strict sort validation
    - Role-based data visibility
    """
    if pageSize is not None:
        page_size = pageSize

    owner_filter = None if current_user.role != RoleEnum.NORMAL else current_user.id

    if current_user.role == RoleEnum.GUEST:
        page_size = min(page_size, 25)

    if sort_by not in TRANSACTION_SORTABLE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported sort column")
    
    if sort_dir.lower() not in {"asc", "desc"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported sort direction")

    if search_mode not in {"contains", "exact"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported search mode")
    if search:
        search = search.strip() or None
    
    table = build_scope_table(settings, "clean")
    return get_transactions(
        client,
        table,
        current_user.tenant_id,
        owner_filter,
        page,
        page_size,
        sort_by,
        sort_dir,
        search,
        search_mode,
    )

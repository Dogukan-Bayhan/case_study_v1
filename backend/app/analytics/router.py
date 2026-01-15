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
    AdHocRequest,
    AdHocResponse,
    BreakdownRow,
    CustomerSegment,
    FilterOption,
    KPIs,
    TimeSeriesPoint,
    TopProduct,
    TransactionPage,
)
from app.analytics.service import (
    get_ad_hoc_results,
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
    """Normalize and enforce analytics scope based on user role.

    Business purpose:
        Ensure analytics queries only access permitted data scopes.
    Why it exists:
        Centralizes scope validation and role-based restrictions.
    Where used:
        All analytics endpoints that accept a scope parameter.
    Inputs:
        scope: Requested scope string ("clean", "issues", "all").
        current_user: Authenticated user used for role enforcement.
    Returns:
        Normalized scope string that is safe to use in queries.
    """
    normalized = scope.lower()
    # Reject unsupported scopes to avoid accidental table access.
    if normalized not in SCOPE_VALUES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported scope")
    # NORMAL users are restricted to clean data regardless of request.
    if current_user.role == RoleEnum.NORMAL and normalized != "clean":
        return "clean"
    return normalized


def _parse_filters(request: Request) -> dict[str, object]:
    """Parse dashboard filter query parameters into a filter map.

    Business purpose:
        Convert HTTP query parameters into a structured filter dict.
    Why it exists:
        Keeps filter parsing consistent across analytics endpoints.
    Where used:
        FastAPI dependency in analytics endpoints.
    Inputs:
        request: Incoming HTTP request containing query parameters.
    Returns:
        Dict of filter keys to typed values.
    """
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
    """Return KPI aggregates for the dashboard tiles.

    Business purpose:
        Provide revenue, orders, AOV, and customer counts for summary cards.
    Why it exists:
        Encapsulates KPI retrieval and tenant/role scoping in one endpoint.
    Where used:
        Dashboard KPI tiles on the main analytics page.
    Inputs:
        scope: Requested analytics scope ("clean", "issues", "all").
        current_user: Authenticated user for tenant and role scoping.
        client: ClickHouse client for analytics queries.
        settings: App settings used to resolve scope tables.
        filters: Parsed dashboard filters from query params.
    Returns:
        KPIs response model with aggregated metrics.
    """
    # NORMAL users are restricted to their own data; others see tenant-wide data.
    owner_filter = None if current_user.role != RoleEnum.NORMAL else current_user.id
    # Resolve the analytics fact table based on scope and user role.
    table = build_scope_table(settings, _resolve_scope(scope, current_user))
    # Delegate aggregation to the analytics service layer.
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
    """Return time-series points for chart visualizations.

    Business purpose:
        Power charting for revenue/orders over time.
    Why it exists:
        Provides a validated, scoped entrypoint for time-series queries.
    Where used:
        Dashboard chart widgets.
    Inputs:
        metric: Metric key to aggregate (revenue, orders, customers).
        grain: Time grain for grouping (day, week, month).
        scope: Requested analytics scope.
        current_user: Authenticated user for scoping.
        client: ClickHouse client for analytics queries.
        settings: App settings used to resolve scope tables.
        filters: Parsed dashboard filters from query params.
    Returns:
        List of TimeSeriesPoint entries for charting.
    """
    # Apply owner-level filtering only for NORMAL users.
    owner_filter = None if current_user.role != RoleEnum.NORMAL else current_user.id
    # Resolve the correct fact table before querying.
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
    """Return a ranked list of top products by metric.

    Business purpose:
        Provide product leaderboards for revenue or quantity insights.
    Why it exists:
        Encapsulates ranking logic with consistent scope enforcement.
    Where used:
        Dashboard leaderboard widgets.
    Inputs:
        limit: Maximum number of products to return.
        metric: Metric key for ranking (revenue or quantity).
        scope: Requested analytics scope.
        current_user: Authenticated user for scoping.
        client: ClickHouse client for analytics queries.
        settings: App settings used to resolve scope tables.
        filters: Parsed dashboard filters from query params.
    Returns:
        List of TopProduct entries ordered by the requested metric.
    """
    # Apply owner-level filtering only for NORMAL users.
    owner_filter = None if current_user.role != RoleEnum.NORMAL else current_user.id
    # Resolve the correct fact table based on scope.
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
    """Return aggregated breakdown rows for a single dimension.

    Business purpose:
        Provide rollup tables by country, category, department, etc.
    Why it exists:
        Exposes grouped analytics in a controlled and validated way.
    Where used:
        Dashboard breakdown widgets.
    Inputs:
        dimension: Grouping dimension key.
        limit: Maximum number of groups to return.
        scope: Requested analytics scope.
        current_user: Authenticated user for scoping.
        client: ClickHouse client for analytics queries.
        settings: App settings used to resolve scope tables.
        filters: Parsed dashboard filters from query params.
    Returns:
        List of BreakdownRow entries.
    """
    # Apply owner-level filtering only for NORMAL users.
    owner_filter = None if current_user.role != RoleEnum.NORMAL else current_user.id
    # Resolve the correct fact table based on scope.
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
    """Return new vs returning customer segment aggregates.

    Business purpose:
        Surface customer mix metrics for retention insights.
    Why it exists:
        Provides a single endpoint for segment aggregation.
    Where used:
        Dashboard customer segment widget.
    Inputs:
        scope: Requested analytics scope.
        current_user: Authenticated user for scoping.
        client: ClickHouse client for analytics queries.
        settings: App settings used to resolve scope tables.
        filters: Parsed dashboard filters from query params.
    Returns:
        List of CustomerSegment entries for New and Returning segments.
    """
    # Apply owner-level filtering only for NORMAL users.
    owner_filter = None if current_user.role != RoleEnum.NORMAL else current_user.id
    # Resolve the correct fact table based on scope.
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
    """Return distinct values for a filter field with optional search.

    Business purpose:
        Provide autocomplete suggestions for filter inputs.
    Why it exists:
        Keeps filter option retrieval consistent and scoped by tenant/filters.
    Where used:
        Dashboard and Slice & Dice filter auto-complete fields.
    Inputs:
        field: Filter field name to fetch values for.
        q: Optional search substring to narrow results.
        limit: Maximum number of options to return.
        scope: Requested analytics scope.
        current_user: Authenticated user for scoping.
        client: ClickHouse client for analytics queries.
        settings: App settings used to resolve scope tables.
        filters: Parsed dashboard filters from query params.
    Returns:
        List of FilterOption values for the requested field.
    """
    # Only allow fields that have been explicitly whitelisted.
    if field not in FILTER_OPTION_FIELDS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported filter field")
    # Prevent the field being queried from filtering itself.
    if field in filters:
        filters.pop(field)
    # Normalize the search string to avoid empty predicates.
    if q:
        q = q.strip() or None
    # Apply owner-level filtering only for NORMAL users.
    owner_filter = None if current_user.role != RoleEnum.NORMAL else current_user.id
    # Resolve the correct fact table based on scope.
    table = build_scope_table(settings, _resolve_scope(scope, current_user))
    # Build tenant and filter scoped WHERE clause.
    where, params = build_where_clause(current_user.tenant_id, owner_filter, filters)
    params["limit"] = limit
    if q:
        # Use case-insensitive substring match for autocomplete.
        params["search"] = q
        where += f" AND positionCaseInsensitive({field}, %(search)s) > 0"
    # Query returns distinct values with a hard limit for safety.
    # Distinct + LIMIT keeps autocomplete responsive on large datasets.
    # WHERE clause enforces tenant isolation and optional filters.
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
    """Return paginated transactions for the explorer table.

    Business purpose:
        Serve transaction-level data to the UI with pagination and sorting.
    Why it exists:
        Centralizes validation for pagination, sorting, and role-based limits.
    Where used:
        Transactions explorer page and API clients.
    Inputs:
        page: 1-based page index.
        page_size: Requested page size.
        pageSize: Optional alias from the UI for page size.
        sort_by: Sort column key.
        sort_dir: Sort direction (asc/desc).
        search: Optional transaction_id search term.
        search_mode: Search mode ("contains" or "exact").
        current_user: Authenticated user for scoping and caps.
        client: ClickHouse client for analytics queries.
        settings: App settings used to resolve tables.
    Returns:
        TransactionPage payload with rows and total count.
    """
    # Allow UI alias to override the default page_size parameter.
    if pageSize is not None:
        page_size = pageSize

    # NORMAL users are scoped to their own transactions.
    owner_filter = None if current_user.role != RoleEnum.NORMAL else current_user.id

    # GUEST users are capped to reduce exposure and query load.
    if current_user.role == RoleEnum.GUEST:
        page_size = min(page_size, 25)

    # Validate sort column and direction to prevent unsafe queries.
    if sort_by not in TRANSACTION_SORTABLE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported sort column")
    
    if sort_dir.lower() not in {"asc", "desc"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported sort direction")

    # Validate search mode and normalize the search string.
    if search_mode not in {"contains", "exact"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported search mode")
    if search:
        search = search.strip() or None
    
    # Transactions endpoint always reads from clean facts for consistency.
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


# ---------------------------------------------------------------------
# AD-HOC ANALYTICS ENDPOINT (Slice & Dice Studio)
# ---------------------------------------------------------------------
@router.post("/ad-hoc", response_model=AdHocResponse)
def ad_hoc(
    payload: AdHocRequest,
    current_user: User = Depends(get_current_user),
    client=Depends(get_clickhouse),
    settings=Depends(get_settings),
) -> AdHocResponse:
    """Execute ad-hoc analytics for the Slice & Dice Studio.

    Business purpose:
        Allow analysts to build custom metrics/dimensions in a single query.
    Why it exists:
        Provides a validated, performance-safe entrypoint for ad-hoc analytics.
    Where used:
        Slice & Dice Studio page.
    Inputs:
        payload: AdHocRequest containing scope, metrics, dimensions, and filters.
        current_user: Authenticated user for tenant and role scoping.
        client: ClickHouse client for analytics queries.
        settings: App settings used to resolve scope tables.
    Returns:
        AdHocResponse with columns, rows, and pagination metadata.
    """
    # Normalize and enforce scope based on the user's role.
    scope = _resolve_scope(payload.scope, current_user)
    # NORMAL users are scoped to their own transactions.
    owner_filter = None if current_user.role != RoleEnum.NORMAL else current_user.id

    # Clamp pagination inputs to guard against unbounded queries.
    limit = max(1, min(payload.limit, 500))
    offset = max(0, payload.offset)
    # Normalize sort direction and validate accepted values.
    sort_dir = payload.sort_dir.lower()
    if sort_dir not in {"asc", "desc"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported sort direction")

    # Resolve the fact table based on requested scope.
    table = build_scope_table(settings, scope)
    try:
        return get_ad_hoc_results(
            client,
            table,
            current_user.tenant_id,
            owner_filter,
            scope,
            payload.metrics,
            payload.dimensions,
            payload.date_grain or "day",
            payload.filters,
            limit,
            offset,
            payload.sort_by,
            sort_dir,
        )
    except ValueError as exc:
        # Convert validation errors into a client-friendly 400 response.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

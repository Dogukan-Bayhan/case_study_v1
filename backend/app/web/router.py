"""Web UI routes using Jinja2 templates."""

from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.analytics.queries import TRANSACTION_COLUMNS, TRANSACTION_SORTABLE
from app.analytics.service import get_transactions
from app.auth.service import authenticate_user, create_user, issue_guest_token, issue_token
from app.core.config import Settings
from app.core.deps import get_clickhouse, get_current_user, get_db, get_settings
from app.db.clickhouse import fact_table
from app.db.models import QualityFinding, QualityReport, RoleEnum, Tenant, User

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="/app/app/web/templates")

TRANSACTION_LABELS = {
    "transaction_id": "Transaction ID",
    "customer_id": "Customer ID",
    "customer_name": "Customer Name",
    "email": "Email",
    "phone": "Phone",
    "country": "Country",
    "city": "City",
    "postal_code": "Postal Code",
    "department": "Department",
    "category": "Category",
    "product_name": "Product Name",
    "product_code": "Product Code",
    "quantity": "Quantity",
    "unit_price": "Unit Price",
    "discount_percent": "Discount %",
    "tax_rate": "Tax Rate",
    "payment_method": "Payment Method",
    "status": "Status",
    "tier": "Tier",
    "order_date": "Order Date",
    "is_returning_customer": "Returning",
    "loyalty_points": "Loyalty Points",
    "rating": "Rating",
    "region_code": "Region",
    "sales_rep_id": "Sales Rep ID",
    "total_amount": "Total Amount",
}


def _transaction_columns() -> list[dict[str, object]]:
    """Build column metadata for the transactions table UI.

    Business purpose:
        Provide labels and sortability info for the transactions table.
    Why it exists:
        Centralizes column definitions in one backend helper.
    Where used:
        Transactions page template rendering.
    Inputs:
        None; uses TRANSACTION_COLUMNS and TRANSACTION_LABELS constants.
    Returns:
        List of column metadata dictionaries.
    """
    columns = []
    for key in TRANSACTION_COLUMNS:
        columns.append(
            {
                "key": key,
                "label": TRANSACTION_LABELS.get(key, key.replace("_", " ").title()),
                "sortable": key in TRANSACTION_SORTABLE,
            }
        )
    return columns


def _pagination_state(page: int, page_size: int, total: int) -> dict[str, int | None]:
    """Compute pagination metadata for template navigation controls.

    Business purpose:
        Provide prev/next page values for the transactions UI.
    Why it exists:
        Keeps pagination math consistent with API results.
    Where used:
        Transactions page and HTMX partials.
    Inputs:
        page: Current page index (1-based).
        page_size: Number of rows per page.
        total: Total number of matching rows.
    Returns:
        Dict with total_pages, prev_page, and next_page values.
    """
    # Compute total pages with a floor of 1 to avoid zero-page UIs.
    total_pages = max(1, (total + page_size - 1) // page_size)
    prev_page = page - 1 if page > 1 else None
    next_page = page + 1 if page < total_pages else None
    return {"total_pages": total_pages, "prev_page": prev_page, "next_page": next_page}


def _resolve_tenant(db: Session, tenant_slug: str | None) -> Tenant:
    """Resolve a tenant by slug or fall back to the first tenant."""
    query = db.query(Tenant)
    if tenant_slug:
        tenant = query.filter(Tenant.slug == tenant_slug).first()
        if tenant:
            return tenant
    tenant = query.order_by(Tenant.id.asc()).first()
    if not tenant:
        raise ValueError("No tenant available")
    return tenant


def _access_denied(request: Request, current_user: User | None = None):
    """Render an access denied template for unauthorized web routes."""
    return templates.TemplateResponse(
        "access_denied.html",
        {"request": request, "user": current_user},
        status_code=status.HTTP_403_FORBIDDEN,
    )


@router.get("/login")
def login_page(request: Request):
    """Render the login page template.

    Business purpose:
        Present the authentication form for web users.
    Why it exists:
        Provides the entrypoint for web session authentication.
    Where used:
        GET /login in the web UI.
    Inputs:
        request: FastAPI Request for template rendering context.
    Returns:
        TemplateResponse for the login page.
    """
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/signup")
def signup_page(request: Request):
    """Render the sign-up page template."""
    return templates.TemplateResponse("signup.html", {"request": request})


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Authenticate credentials and establish a web session.

    Business purpose:
        Exchange login form credentials for a session cookie.
    Why it exists:
        Handles web login without duplicating API auth logic.
    Where used:
        POST /login from the login form.
    Inputs:
        request: FastAPI Request for template rendering context.
        email: User email from the form.
        password: User password from the form.
        db: SQLAlchemy session for user lookup.
        settings: Security settings used to sign the JWT.
    Returns:
        RedirectResponse to the dashboard with a session cookie, or error view.
    """
    # Authenticate credentials using shared auth service.
    user = authenticate_user(db, email, password)
    if user is None:
        # Return the login template with an error message on failure.
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid credentials"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    # Issue a JWT and store it as an HTTP-only cookie.
    token = issue_token(user, settings)
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie("access_token", token, httponly=True, samesite="lax")
    return response


@router.post("/signup")
def signup_submit(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    tenant_slug: str | None = Form(None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Register a normal user account and establish a web session."""
    try:
        tenant = _resolve_tenant(db, tenant_slug)
    except ValueError:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Tenant not available."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    existing = (
        db.query(User)
        .filter(User.tenant_id == tenant.id, User.email == email)
        .first()
    )
    if existing:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Email already registered."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    user = create_user(db, email, password, tenant.id, RoleEnum.NORMAL, full_name=full_name)
    token = issue_token(user, settings)
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie("access_token", token, httponly=True, samesite="lax")
    return response


@router.post("/guest")
def guest_access(
    request: Request,
    tenant_slug: str | None = Form(None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Start a guest session and redirect to the dashboard."""
    try:
        tenant = _resolve_tenant(db, tenant_slug)
    except ValueError:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Guest access is unavailable."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    token, _ = issue_guest_token(tenant, settings)
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie("access_token", token, httponly=True, samesite="lax")
    return response


@router.get("/logout")
def logout() -> RedirectResponse:
    """Clear the session cookie and redirect to the login page.

    Business purpose:
        End the web session for the current user.
    Why it exists:
        Provides a simple logout mechanism for the UI.
    Where used:
        GET /logout from the navigation menu.
    Inputs:
        None.
    Returns:
        RedirectResponse to /login with the session cookie cleared.
    """
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("access_token")
    return response


@router.get("/")
def dashboard(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Render the analytics dashboard shell.

    Business purpose:
        Serve the main dashboard page for analytics visualizations.
    Why it exists:
        Provides a server-rendered entrypoint for the dashboard UI.
    Where used:
        GET / for web users.
    Inputs:
        request: FastAPI Request for template rendering context.
        current_user: Authenticated user for personalization.
    Returns:
        TemplateResponse for the dashboard page.
    """
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": current_user,
        },
    )


@router.get("/slice-dice")
def slice_dice_page(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Render the Slice & Dice Studio page shell.

    Business purpose:
        Provide the ad-hoc analytics builder UI.
    Why it exists:
        Serves a dedicated page for ad-hoc analytics exploration.
    Where used:
        GET /slice-dice for web users.
    Inputs:
        request: FastAPI Request for template rendering context.
        current_user: Authenticated user for personalization.
    Returns:
        TemplateResponse for the Slice & Dice Studio page.
    """
    return templates.TemplateResponse(
        "slice_dice.html",
        {
            "request": request,
            "user": current_user,
        },
    )


@router.get("/quality")
def quality_page(request: Request, current_user: User = Depends(get_current_user)):
    """Render the quality dashboard page with the latest report data.

    Business purpose:
        Serve the quality dashboard shell with summary context.
    Why it exists:
        Provides server-rendered entrypoint for quality analytics.
    Where used:
        GET /quality in the web UI.
    Inputs:
        request: FastAPI Request for template rendering context.
        current_user: Authenticated user for tenant scoping.
    Returns:
        TemplateResponse for the quality dashboard page.
    """
    # Fetch the latest report for the tenant for initial rendering.
    db = request.app.state.session_maker()
    report = (
        db.query(QualityReport)
        .filter(QualityReport.tenant_id == current_user.tenant_id)
        .order_by(QualityReport.created_at.desc())
        .first()
    )
    db.close()
    return templates.TemplateResponse(
        "quality.html",
        {
            "request": request,
            "user": current_user,
            "report": report,
        },
    )


@router.get("/quality/partial")
def quality_partial(request: Request, current_user: User = Depends(get_current_user)):
    """Render the findings table partial for HTMX refreshes.

    Business purpose:
        Provide an HTMX fragment for quality findings refreshes.
    Why it exists:
        Avoids re-rendering the full page when only findings change.
    Where used:
        HTMX call from the quality page.
    Inputs:
        request: FastAPI Request for template rendering context.
        current_user: Authenticated user for tenant scoping.
    Returns:
        TemplateResponse for the findings table partial.
    """
    # Fetch the latest report and associated findings for the tenant.
    db = request.app.state.session_maker()
    report = (
        db.query(QualityReport)
        .filter(QualityReport.tenant_id == current_user.tenant_id)
        .order_by(QualityReport.created_at.desc())
        .first()
    )
    findings = []
    if report:
        # Join findings to the latest report only.
        findings = db.query(QualityFinding).filter(QualityFinding.report_id == report.id).all()
    db.close()
    return templates.TemplateResponse(
        "partials/quality_table.html",
        {"request": request, "findings": findings},
    )


@router.get("/admin")
def admin_page(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Render the admin user management page.

    Business purpose:
        Provide tenant admins with user management UI.
    Why it exists:
        Serves a protected admin-only page.
    Where used:
        GET /admin for admin users.
    Inputs:
        request: FastAPI Request for template rendering context.
        current_user: Authenticated user for role-based access checks.
    Returns:
        TemplateResponse for the admin page.
    """
    if current_user.role != RoleEnum.ADMIN:
        return _access_denied(request, current_user)

    # Load all users in the current tenant for display.
    db = request.app.state.session_maker()
    users = db.query(User).filter(User.tenant_id == current_user.tenant_id).all()
    db.close()
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "user": current_user, "users": users},
    )


@router.get("/transactions")
def transactions_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    sort_by: str = Query("order_date"),
    sort_dir: str = Query("desc"),
    search: str | None = Query(None),
    search_mode: str = Query("contains"),
    current_user: User = Depends(get_current_user),
    client=Depends(get_clickhouse),
    settings: Settings = Depends(get_settings),
):
    """Render the transactions page with server-side pagination.

    Business purpose:
        Provide a server-rendered transactions explorer UI.
    Why it exists:
        Keeps pagination and sorting logic consistent with the API.
    Where used:
        GET /transactions in the web UI.
    Inputs:
        request: FastAPI Request for template rendering context.
        page: Page index (1-based).
        page_size: Number of rows per page.
        sort_by: Sort column key.
        sort_dir: Sort direction (asc/desc).
        search: Optional transaction_id search term.
        search_mode: Search mode ("contains" or "exact").
        current_user: Authenticated user for role-based scoping.
        client: ClickHouse client for analytics queries.
        settings: App settings used to resolve table names.
    Returns:
        TemplateResponse with transaction data and pagination metadata.
    """
    # Normalize sort inputs to safe defaults.
    if sort_by not in TRANSACTION_SORTABLE:
        sort_by = "order_date"
    if sort_dir.lower() not in {"asc", "desc"}:
        sort_dir = "desc"
    if search_mode not in {"contains", "exact"}:
        search_mode = "contains"
    if search:
        search = search.strip() or None

    # Always query the clean fact table for transaction rows.
    table = fact_table(settings)
    # NORMAL users only see their own transactions.
    owner_filter = current_user.id if current_user.role == RoleEnum.NORMAL else None
    page_sizes = [25, 50, 100]
    if current_user.role == RoleEnum.GUEST:
        # Guests are capped to reduce load and limit exposure.
        page_size = min(page_size, 25)
        page_sizes = [25]

    data = get_transactions(
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
    pagination = _pagination_state(data.page, data.page_size, data.total)
    return templates.TemplateResponse(
        "transactions.html",
        {
            "request": request,
            "user": current_user,
            "data": data,
            "columns": _transaction_columns(),
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "page_sizes": page_sizes,
            "search": search or "",
            "search_mode": search_mode,
            **pagination,
        },
    )


@router.get("/transactions/partial")
def transactions_partial(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    sort_by: str = Query("order_date"),
    sort_dir: str = Query("desc"),
    search: str | None = Query(None),
    search_mode: str = Query("contains"),
    current_user: User = Depends(get_current_user),
    client=Depends(get_clickhouse),
    settings: Settings = Depends(get_settings),
):
    """Serve the paginated transactions table fragment for HTMX.

    Business purpose:
        Refresh the transactions table without reloading the whole page.
    Why it exists:
        Enables HTMX partial updates with consistent pagination logic.
    Where used:
        HTMX requests from the transactions page.
    Inputs:
        request: FastAPI Request for template rendering context.
        page: Page index (1-based).
        page_size: Number of rows per page.
        sort_by: Sort column key.
        sort_dir: Sort direction (asc/desc).
        search: Optional transaction_id search term.
        search_mode: Search mode ("contains" or "exact").
        current_user: Authenticated user for role-based scoping.
        client: ClickHouse client for analytics queries.
        settings: App settings used to resolve table names.
    Returns:
        TemplateResponse containing the table fragment.
    """
    # Normalize sort inputs to safe defaults.
    if sort_by not in TRANSACTION_SORTABLE:
        sort_by = "order_date"
    if sort_dir.lower() not in {"asc", "desc"}:
        sort_dir = "desc"
    if search_mode not in {"contains", "exact"}:
        search_mode = "contains"
    if search:
        search = search.strip() or None

    # Always query the clean fact table for transaction rows.
    table = fact_table(settings)
    # NORMAL users only see their own transactions.
    owner_filter = current_user.id if current_user.role == RoleEnum.NORMAL else None
    page_sizes = [25, 50, 100]
    if current_user.role == RoleEnum.GUEST:
        # Guests are capped to reduce load and limit exposure.
        page_size = min(page_size, 25)
        page_sizes = [25]

    data = get_transactions(
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
    pagination = _pagination_state(data.page, data.page_size, data.total)
    return templates.TemplateResponse(
        "partials/transactions_table.html",
        {
            "request": request,
            "user": current_user,
            "data": data,
            "columns": _transaction_columns(),
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "page_sizes": page_sizes,
            "search": search or "",
            "search_mode": search_mode,
            **pagination,
        },
    )

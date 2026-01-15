"""Web UI routes using Jinja2 templates."""

from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.analytics.queries import TRANSACTION_COLUMNS, TRANSACTION_SORTABLE
from app.analytics.service import get_kpis, get_timeseries, get_top_products, get_transactions
from app.auth.service import authenticate_user, issue_token
from app.core.config import Settings
from app.core.deps import get_clickhouse, get_current_user, get_db, get_settings, require_role
from app.db.clickhouse import fact_table
from app.db.models import QualityFinding, QualityReport, RoleEnum, User

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
    """Build the column metadata used by the transactions table."""
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
    """Compute pagination boundaries for template navigation."""
    total_pages = max(1, (total + page_size - 1) // page_size)
    prev_page = page - 1 if page > 1 else None
    next_page = page + 1 if page < total_pages else None
    return {"total_pages": total_pages, "prev_page": prev_page, "next_page": next_page}


@router.get("/login")
def login_page(request: Request):
    """Render the login form."""
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Authenticate a user and set the session cookie."""
    user = authenticate_user(db, email, password)
    if user is None:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid credentials"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    token = issue_token(user, settings)
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie("access_token", token, httponly=True, samesite="lax")
    return response


@router.get("/logout")
def logout() -> RedirectResponse:
    """Clear the session cookie and redirect to login."""
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("access_token")
    return response


@router.get("/")
def dashboard(
    request: Request,
    current_user: User = Depends(get_current_user),
    client=Depends(get_clickhouse),
    settings: Settings = Depends(get_settings),
):
    """Render the dashboard with tenant-scoped analytics."""
    table = fact_table(settings)
    owner_filter = current_user.id if current_user.role == RoleEnum.NORMAL else None
    kpi_data = get_kpis(client, table, current_user.tenant_id, owner_filter)
    series = get_timeseries(client, "revenue", "day", table, current_user.tenant_id, owner_filter)
    top = get_top_products(client, table, current_user.tenant_id, owner_filter, 10)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": current_user,
            "kpis": kpi_data,
            "series": series,
            "top_products": top,
        },
    )


@router.get("/quality")
def quality_page(request: Request, current_user: User = Depends(get_current_user)):
    """Render the quality page shell with the latest report."""
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
    """Render the findings partial for HTMX refreshes."""
    db = request.app.state.session_maker()
    report = (
        db.query(QualityReport)
        .filter(QualityReport.tenant_id == current_user.tenant_id)
        .order_by(QualityReport.created_at.desc())
        .first()
    )
    findings = []
    if report:
        findings = db.query(QualityFinding).filter(QualityFinding.report_id == report.id).all()
    db.close()
    return templates.TemplateResponse(
        "partials/quality_table.html",
        {"request": request, "findings": findings},
    )


@router.get("/admin")
def admin_page(
    request: Request,
    current_user: User = Depends(require_role(RoleEnum.ADMIN)),
):
    """Render the admin view for tenant user management."""
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
    current_user: User = Depends(get_current_user),
    client=Depends(get_clickhouse),
    settings: Settings = Depends(get_settings),
):
    """Render the transactions page with server-side pagination."""
    if sort_by not in TRANSACTION_SORTABLE:
        sort_by = "order_date"
    if sort_dir.lower() not in {"asc", "desc"}:
        sort_dir = "desc"

    table = fact_table(settings)
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
    current_user: User = Depends(get_current_user),
    client=Depends(get_clickhouse),
    settings: Settings = Depends(get_settings),
):
    """Serve the paginated transactions table fragment for HTMX."""
    if sort_by not in TRANSACTION_SORTABLE:
        sort_by = "order_date"
    if sort_dir.lower() not in {"asc", "desc"}:
        sort_dir = "desc"

    table = fact_table(settings)
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
            **pagination,
        },
    )

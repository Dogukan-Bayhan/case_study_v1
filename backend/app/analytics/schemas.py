"""Analytics response schemas."""

from pydantic import BaseModel, ConfigDict


def _to_camel(value: str) -> str:
    """Translate snake_case fields to camelCase for frontend ergonomics."""
    parts = value.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


class CamelModel(BaseModel):
    """Base model that exposes camelCase aliases for JSON responses."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
    )


class KPIs(BaseModel):
    """Summary KPI payload for dashboard cards."""
    revenue: float
    orders: int
    avg_order_value: float
    unique_customers: int


class TimeSeriesPoint(BaseModel):
    """Single point in a KPI time series."""
    bucket: str
    value: float


class TopProduct(BaseModel):
    """Top product record in aggregate analytics."""
    product: str
    metric: str
    value: float


class BreakdownRow(BaseModel):
    """Aggregated breakdown row for a grouping dimension."""
    key: str
    revenue: float
    orders: int
    avg_order_value: float
    quantity: float


class CustomerSegment(BaseModel):
    """Aggregate metrics for a customer segment."""
    segment: str
    customers: int
    orders: int
    revenue: float
    avg_order_value: float


class FilterOption(BaseModel):
    """Autocomplete value for filter fields."""
    value: str


class TransactionRow(CamelModel):
    """Flattened transaction row returned to the UI."""
    transaction_id: str | None
    customer_id: str | None
    customer_name: str | None
    email: str | None
    phone: str | None
    country: str | None
    city: str | None
    postal_code: str | None
    department: str | None
    category: str | None
    product_name: str | None
    product_code: str | None
    quantity: float | None
    unit_price: float | None
    discount_percent: float | None
    tax_rate: float | None
    payment_method: str | None
    status: str | None
    tier: str | None
    order_date: str | None
    is_returning_customer: int | None
    loyalty_points: float | None
    rating: float | None
    region_code: str | None
    sales_rep_id: str | None
    total_amount: float | None


class TransactionPage(CamelModel):
    """Paginated transaction listing for server-side pagination."""
    page: int
    page_size: int
    total: int
    rows: list[TransactionRow]

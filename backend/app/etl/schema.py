"""Canonical schema mapping for e-commerce data."""

def normalize_column(name: str) -> str:
    """Normalize incoming column names so mapping is deterministic."""
    return (
        name.strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace(".", "_")
    )

REQUIRED_TRANSACTION_COLUMNS = [
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

# Explicit CSV-to-canonical mapping using the dataset headers as the source of truth.
CSV_TO_CANONICAL = {
    "transaction_id": "transaction_id",
    "customer_id": "customer_id",
    "customer_name": "customer_name",
    "email": "email",
    "phone": "phone",
    "country": "country",
    "city": "city",
    "postal_code": "postal_code",
    "department": "department",
    "category": "category",
    "product_name": "product_name",
    "product_code": "product_code",
    "quantity": "quantity",
    "unit_price": "unit_price",
    "discount_percent": "discount_percent",
    "tax_rate": "tax_rate",
    "payment_method": "payment_method",
    "status": "status",
    "tier": "tier",
    "order_date": "order_date",
    "is_returning_customer": "is_returning_customer",
    "loyalty_points": "loyalty_points",
    "rating": "rating",
    "region_code": "region_code",
    "sales_rep_id": "sales_rep_id",
    "total_amount": "total_amount",
}


CANONICAL_MAP = {
    "transaction_id": ["transaction_id", "order_id", "invoice_id", "order_number"],
    "customer_id": ["customer_id", "customerid", "client_id", "buyer_id"],
    "customer_name": ["customer_name", "client_name", "buyer_name", "customer"],
    "email": ["email", "email_address", "customer_email"],
    "phone": ["phone", "phone_number", "mobile", "mobile_number"],
    "country": ["country", "country_name"],
    "city": ["city", "town"],
    "postal_code": ["postal_code", "zip", "zip_code", "postcode"],
    "department": ["department", "dept"],
    "category": ["category", "product_category"],
    "product_name": ["product_name", "item_name", "product", "item"],
    "product_code": ["product_code", "sku", "item_code", "sku_code"],
    "product_id": ["product_id", "item_id"],
    "quantity": ["quantity", "qty", "item_qty"],
    "unit_price": ["unit_price", "unitprice", "unit_cost"],
    "price": ["price"],
    "discount_percent": ["discount_percent", "discount_pct", "discount_rate", "discount"],
    "tax_rate": ["tax_rate", "vat_rate", "vat", "tax"],
    "payment_method": ["payment_method", "payment", "payment_type", "payment_mode"],
    "status": ["status", "order_status"],
    "tier": ["tier", "customer_tier", "loyalty_tier"],
    "order_date": ["order_date", "date", "order_timestamp", "order_time"],
    "is_returning_customer": ["is_returning_customer", "returning_customer", "is_repeat", "repeat_customer"],
    "loyalty_points": ["loyalty_points", "points", "reward_points"],
    "rating": ["rating", "review_rating", "score"],
    "region_code": ["region_code", "region", "state_code", "state"],
    "sales_rep_id": ["sales_rep_id", "sales_rep", "rep_id", "salesperson_id"],
    "total_amount": ["total_amount", "order_total", "total", "revenue"],
    "user_id": ["user_id"],
    "amount": ["amount"],
    "event_ts": ["event_ts", "event_time", "event_timestamp"],
}


def detect_mapping(columns: list[str]) -> dict[str, str]:
    """Infer canonical column mapping from a list of CSV headers."""
    normalized = {normalize_column(col): col for col in columns}
    mapping: dict[str, str] = {}
    for canonical, aliases in CANONICAL_MAP.items():
        for alias in aliases:
            if alias in normalized:
                mapping[canonical] = normalized[alias]
                break
    return mapping


def build_explicit_mapping(columns: list[str]) -> tuple[dict[str, str], list[str]]:
    """Build a canonical->raw mapping based on explicit CSV headers."""
    normalized_csv = {normalize_column(col): col for col in columns}
    normalized_map = {normalize_column(raw): canonical for raw, canonical in CSV_TO_CANONICAL.items()}
    mapping: dict[str, str] = {}
    unknown: list[str] = []
    for normalized, raw in normalized_csv.items():
        if normalized in normalized_map:
            mapping[normalized_map[normalized]] = raw
        else:
            unknown.append(raw)

    # Missing required fields means downstream analytics would be unsafe.
    missing = [col for col in REQUIRED_TRANSACTION_COLUMNS if col not in mapping]
    if missing:
        raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")
    return mapping, unknown

"""Helpers for parsing analytics filter query parameters."""

from __future__ import annotations

from typing import Mapping

STRING_FILTER_FIELDS = {
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
    "payment_method",
    "status",
    "tier",
    "region_code",
    "sales_rep_id",
}

BOOLEAN_FILTER_FIELDS = {"is_returning_customer"}

NUMERIC_FILTER_FIELDS = {
    "quantity",
    "unit_price",
    "discount_percent",
    "tax_rate",
    "loyalty_points",
    "rating",
    "total_amount",
}

DATE_FILTER_FIELDS = {"order_date"}

FILTER_OPTION_FIELDS = sorted(STRING_FILTER_FIELDS)


def _coerce_bool(value: str) -> int | None:
    """Normalize boolean query string values to ClickHouse-friendly integers.

    Business purpose:
        Convert query parameter booleans into the 0/1 values stored in ClickHouse.
    Why it exists:
        Filter values arrive as strings from HTTP query parameters.
    Where used:
        parse_filters when handling dashboard filter inputs.
    Inputs:
        value: Raw string from query params.
    Returns:
        1 or 0 for recognized values, otherwise None.
    """
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return 1
    if normalized in {"0", "false", "no"}:
        return 0
    return None


def _coerce_float(value: str) -> float | None:
    """Parse numeric query parameters into floats.

    Business purpose:
        Support range filters and numeric comparisons in analytics queries.
    Why it exists:
        Query parameters are strings and may be invalid.
    Where used:
        parse_filters for numeric range fields.
    Inputs:
        value: Raw string from query params.
    Returns:
        Float if parsing succeeds, otherwise None.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_filters(params: Mapping[str, str]) -> dict[str, object]:
    """Extract analytics filters from HTTP query parameters.

    Business purpose:
        Translate user-supplied filter inputs into a structured filter map.
    Why it exists:
        Ensures only supported fields are accepted for analytics queries.
    Where used:
        Analytics API endpoints that accept filters via query string.
    Inputs:
        params: Query parameter mapping (usually request.query_params).
    Returns:
        Dict of filter keys to typed values for downstream query builders.
    """
    filters: dict[str, object] = {}
    # Capture string filters as exact-match predicates.
    for field in STRING_FILTER_FIELDS:
        raw_value = params.get(f"filter_{field}")
        if raw_value is None:
            continue
        value = raw_value.strip()
        if value:
            filters[field] = value

    # Normalize booleans to 0/1 to match ClickHouse storage.
    for field in BOOLEAN_FILTER_FIELDS:
        raw_value = params.get(f"filter_{field}")
        if raw_value is None:
            continue
        coerced = _coerce_bool(raw_value)
        if coerced is not None:
            filters[field] = coerced

    # Capture numeric ranges using _min/_max suffixes.
    for field in NUMERIC_FILTER_FIELDS:
        min_value = params.get(f"filter_{field}_min")
        max_value = params.get(f"filter_{field}_max")
        if min_value not in (None, ""):
            coerced = _coerce_float(min_value)
            if coerced is not None:
                filters[f"{field}_min"] = coerced
        if max_value not in (None, ""):
            coerced = _coerce_float(max_value)
            if coerced is not None:
                filters[f"{field}_max"] = coerced

    # Preserve date ranges as strings for ClickHouse parsing.
    for field in DATE_FILTER_FIELDS:
        start_value = params.get(f"filter_{field}_start")
        end_value = params.get(f"filter_{field}_end")
        if start_value not in (None, ""):
            filters[f"{field}_start"] = start_value.strip()
        if end_value not in (None, ""):
            filters[f"{field}_end"] = end_value.strip()

    return filters

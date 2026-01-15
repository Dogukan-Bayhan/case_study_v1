"""Pure, composable data quality rules for ETL validation."""

from __future__ import annotations

from typing import Mapping

RuleResult = tuple[str, str]

RULE_MISSING_REQUIRED = "MISSING_REQUIRED"
RULE_PRICE_MISMATCH = "PRICE_MISMATCH"
RULE_DUPLICATE_TRANSACTION_ID = "DUPLICATE_TRANSACTION_ID"
RULE_SUSPECTED_TYPO = "SUSPECTED_TYPO"

SEVERITY_ERROR = "error"
SEVERITY_INFO = "info"

PRICE_MISMATCH_TOLERANCE_CENTS = 1

REQUIRED_FIELDS = [
    "transaction_id",
    "order_date",
    "total_amount",
    "quantity",
    "unit_price",
]

# Seed reference lists for typo detection; can be extended without changing rule logic.
COUNTRY_REFERENCE = {
    "usa",
    "united states",
    "canada",
    "united kingdom",
    "uk",
    "germany",
    "france",
    "spain",
    "italy",
    "turkey",
    "australia",
    "brazil",
    "india",
    "china",
    "japan",
    "mexico",
    "netherlands",
}

CITY_REFERENCE = {
    "new york",
    "los angeles",
    "chicago",
    "london",
    "berlin",
    "paris",
    "madrid",
    "rome",
    "istanbul",
    "ankara",
    "sydney",
    "melbourne",
    "toronto",
}


def normalize_value(value: object) -> str:
    """Normalize raw values to a comparable lowercase string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip().lower()
    return str(value).strip().lower()


def is_blank(value: object) -> bool:
    """Treat whitespace-only values as missing for rule evaluation."""
    return normalize_value(value) == ""


def missing_required(raw_row: Mapping[str, object]) -> RuleResult | None:
    """Missing required fields make rows unusable for analytics (error)."""
    for field in REQUIRED_FIELDS:
        if is_blank(raw_row.get(field)):
            return (RULE_MISSING_REQUIRED, SEVERITY_ERROR)
    return None


def price_mismatch(raw_row: Mapping[str, object], tolerance_cents: int = PRICE_MISMATCH_TOLERANCE_CENTS) -> RuleResult | None:
    """Detects revenue inconsistencies between quantity, price, discounts, taxes, and totals (error)."""
    quantity = _parse_float(raw_row.get("quantity"))
    unit_price = _parse_float(raw_row.get("unit_price"))
    total_amount = _parse_float(raw_row.get("total_amount"))
    discount_percent = _parse_float(raw_row.get("discount_percent")) or 0.0
    tax_rate = _parse_float(raw_row.get("tax_rate")) or 0.0
    if quantity is None or unit_price is None or total_amount is None:
        return None
    expected_total = (quantity * unit_price) * (1 - discount_percent / 100) * (1 + tax_rate / 100)
    expected_cents = round(expected_total * 100)
    total_cents = round(total_amount * 100)
    if abs(expected_cents - total_cents) > tolerance_cents:
        return (RULE_PRICE_MISMATCH, SEVERITY_ERROR)
    return None


def duplicate_transaction_id(
    transaction_id: object,
    duplicate_ids: set[str],
    existing_ids: set[str],
) -> RuleResult | None:
    """Duplicates create double-counting in analytics (error)."""
    normalized = normalize_value(transaction_id)
    if not normalized:
        return None
    if normalized in duplicate_ids or normalized in existing_ids:
        return (RULE_DUPLICATE_TRANSACTION_ID, SEVERITY_ERROR)
    return None


def suspected_typo(
    raw_row: Mapping[str, object],
    country_reference: set[str] | None = None,
    city_reference: set[str] | None = None,
) -> RuleResult | None:
    """Likely typos are flagged as info to avoid blocking valid data."""
    country_reference = country_reference or COUNTRY_REFERENCE
    city_reference = city_reference or CITY_REFERENCE
    country = normalize_value(raw_row.get("country"))
    city = normalize_value(raw_row.get("city"))
    if suspected_typo_value(country, country_reference) or suspected_typo_value(city, city_reference):
        return (RULE_SUSPECTED_TYPO, SEVERITY_INFO)
    return None


def suspected_typo_value(value: str, reference: set[str]) -> bool:
    """Check whether a value is close to a known reference entry."""
    if not value or value in reference:
        return False
    for candidate in reference:
        if _edit_distance_lte(value, candidate, max_dist=2):
            return True
    return False


def _edit_distance_lte(a: str, b: str, max_dist: int = 2) -> bool:
    """Bounded edit-distance check to keep typo detection fast."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > max_dist:
        return False

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        min_in_row = cur[0]
        for j, cb in enumerate(b, start=1):
            insert_cost = cur[j - 1] + 1
            delete_cost = prev[j] + 1
            replace_cost = prev[j - 1] + (0 if ca == cb else 1)
            cost = min(insert_cost, delete_cost, replace_cost)
            cur.append(cost)
            if cost < min_in_row:
                min_in_row = cost
        if min_in_row > max_dist:
            return False
        prev = cur
    return prev[-1] <= max_dist


def _parse_float(value: object) -> float | None:
    """Parse numeric inputs safely without raising on bad data."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = normalize_value(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None

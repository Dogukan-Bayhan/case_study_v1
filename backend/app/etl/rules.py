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
    """Normalize raw values to a comparable lowercase string.

    Business purpose:
        Provide consistent string normalization for rule checks.
    Why it exists:
        Incoming values may have inconsistent casing or whitespace.
    Where used:
        Quality rules for missing values, duplicates, and typo checks.
    Inputs:
        value: Raw value from a CSV row.
    Returns:
        Lowercased, trimmed string representation.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip().lower()
    return str(value).strip().lower()


def is_blank(value: object) -> bool:
    """Check if a value should be treated as missing.

    Business purpose:
        Detect missing values even when represented as whitespace.
    Why it exists:
        CSV data often contains empty strings instead of nulls.
    Where used:
        Required field validation in quality rules.
    Inputs:
        value: Raw value from a CSV row.
    Returns:
        True if the value is blank after normalization.
    """
    return normalize_value(value) == ""


def missing_required(raw_row: Mapping[str, object]) -> RuleResult | None:
    """Check for missing required fields.

    Business purpose:
        Ensure required transaction fields are present for analytics validity.
    Why it exists:
        Missing identifiers or amounts make rows unsafe for aggregation.
    Where used:
        ETL validation phase to flag critical data issues.
    Inputs:
        raw_row: Raw CSV row values keyed by column name.
    Returns:
        RuleResult for missing required fields, otherwise None.
    """
    for field in REQUIRED_FIELDS:
        if is_blank(raw_row.get(field)):
            return (RULE_MISSING_REQUIRED, SEVERITY_ERROR)
    return None


def price_mismatch(raw_row: Mapping[str, object], tolerance_cents: int = PRICE_MISMATCH_TOLERANCE_CENTS) -> RuleResult | None:
    """Detect mismatches between computed totals and reported totals.

    Business purpose:
        Identify revenue inconsistencies that would skew analytics.
    Why it exists:
        Ensures totals align with quantity, price, discounts, and taxes.
    Where used:
        ETL validation during quality checks.
    Inputs:
        raw_row: Raw CSV row values keyed by column name.
        tolerance_cents: Allowed absolute difference in cents.
    Returns:
        RuleResult when a mismatch exceeds tolerance, otherwise None.
    """
    quantity = _parse_float(raw_row.get("quantity"))
    unit_price = _parse_float(raw_row.get("unit_price"))
    total_amount = _parse_float(raw_row.get("total_amount"))
    discount_percent = _parse_float(raw_row.get("discount_percent")) or 0.0
    tax_rate = _parse_float(raw_row.get("tax_rate")) or 0.0
    # Skip rule when key fields are missing to avoid false positives.
    if quantity is None or unit_price is None or total_amount is None:
        return None
    # Compute expected total including discounts and taxes.
    expected_total = (quantity * unit_price) * (1 - discount_percent / 100) * (1 + tax_rate / 100)
    # Compare in cents to avoid floating point drift.
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
    """Detect duplicate transaction identifiers within or across batches.

    Business purpose:
        Prevent double-counting of transactions in analytics.
    Why it exists:
        Duplicate IDs create inconsistent revenue and order counts.
    Where used:
        ETL validation when ingesting new data.
    Inputs:
        transaction_id: Raw transaction identifier.
        duplicate_ids: IDs seen twice within the current batch.
        existing_ids: IDs already present in the data store.
    Returns:
        RuleResult when a duplicate is detected, otherwise None.
    """
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
    """Flag potential typos in location fields.

    Business purpose:
        Surface likely data entry errors without blocking ingestion.
    Why it exists:
        Country/city typos reduce grouping accuracy but may be recoverable.
    Where used:
        ETL validation as an informational rule.
    Inputs:
        raw_row: Raw CSV row values keyed by column name.
        country_reference: Optional reference set of valid countries.
        city_reference: Optional reference set of valid cities.
    Returns:
        RuleResult when a likely typo is detected, otherwise None.
    """
    country_reference = country_reference or COUNTRY_REFERENCE
    city_reference = city_reference or CITY_REFERENCE
    country = normalize_value(raw_row.get("country"))
    city = normalize_value(raw_row.get("city"))
    # Use bounded edit distance to keep typo detection fast.
    if suspected_typo_value(country, country_reference) or suspected_typo_value(city, city_reference):
        return (RULE_SUSPECTED_TYPO, SEVERITY_INFO)
    return None


def suspected_typo_value(value: str, reference: set[str]) -> bool:
    """Check whether a value is close to a known reference entry.

    Business purpose:
        Identify likely misspellings for reference-matched fields.
    Why it exists:
        Helps flag dirty data while avoiding heavy fuzzy matching libraries.
    Where used:
        suspected_typo rule for countries and cities.
    Inputs:
        value: Normalized input value.
        reference: Set of valid reference strings.
    Returns:
        True when the value is close to a reference entry.
    """
    if not value or value in reference:
        return False
    for candidate in reference:
        if _edit_distance_lte(value, candidate, max_dist=2):
            return True
    return False


def _edit_distance_lte(a: str, b: str, max_dist: int = 2) -> bool:
    """Check if two strings are within a bounded edit distance.

    Business purpose:
        Provide a fast typo detection primitive for small distances.
    Why it exists:
        Full edit distance is expensive; bounding allows early exit.
    Where used:
        suspected_typo_value for country/city checks.
    Inputs:
        a: First string.
        b: Second string.
        max_dist: Maximum allowed edit distance.
    Returns:
        True if edit distance is <= max_dist, otherwise False.
    """
    if a == b:
        return True
    if abs(len(a) - len(b)) > max_dist:
        return False

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        min_in_row = cur[0]
        for j, cb in enumerate(b, start=1):
            # Compute edit costs for insert, delete, replace.
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
    """Parse numeric inputs safely without raising on bad data.

    Business purpose:
        Convert raw numeric fields to floats for rule calculations.
    Why it exists:
        Prevents parsing failures from crashing ETL validation.
    Where used:
        price_mismatch rule and other numeric validations.
    Inputs:
        value: Raw value from CSV.
    Returns:
        Float when parsing succeeds, otherwise None.
    """
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

"""Pure, composable data quality rules for ETL validation."""

from __future__ import annotations

import re
import unicodedata
from typing import Mapping

RuleResult = tuple[str, str]

RULE_MISSING_REQUIRED = "MISSING_REQUIRED"
RULE_PRICE_MISMATCH = "PRICE_MISMATCH"
RULE_DUPLICATE_TRANSACTION_ID = "DUPLICATE_TRANSACTION_ID"
RULE_SUSPECTED_TYPO = "SUSPECTED_TYPO"
RULE_COUNTRY_CITY_MISMATCH = "COUNTRY_CITY_MISMATCH"
RULE_REGION_COUNTRY_MISMATCH = "REGION_COUNTRY_MISMATCH"
RULE_POSTAL_CODE_INVALID = "POSTAL_CODE_INVALID"
RULE_PHONE_COUNTRY_MISMATCH = "PHONE_COUNTRY_MISMATCH"
RULE_FINANCIAL_TOTAL_MISMATCH = "FINANCIAL_TOTAL_MISMATCH"
RULE_STATUS_INVALID = "STATUS_INVALID"
RULE_PAYMENT_METHOD_INVALID = "PAYMENT_METHOD_INVALID"
RULE_STATUS_PAYMENT_INCONSISTENT = "STATUS_PAYMENT_INCONSISTENT"
RULE_DEPARTMENT_INVALID = "DEPARTMENT_INVALID"
RULE_CATEGORY_INVALID = "CATEGORY_INVALID"
RULE_TEMPORAL_UNIFORMITY_DATASET = "TEMPORAL_UNIFORMITY_DATASET"

SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"

PRICE_MISMATCH_TOLERANCE_CENTS = 1
FINANCIAL_MISMATCH_MIN_TOLERANCE = 0.01
FINANCIAL_MISMATCH_REL_TOLERANCE = 0.001
TEMPORAL_UNIFORMITY_THRESHOLD = 0.9

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

# Canonical mappings for location consistency checks.
COUNTRY_ALIASES = {
    "usa": "united states",
    "u.s.a": "united states",
    "uk": "united kingdom",
    "u.k": "united kingdom",
    "turkiye": "turkey",
}

KNOWN_COUNTRIES = {
    "united states",
    "united kingdom",
    "canada",
    "germany",
    "france",
    "spain",
    "italy",
    "netherlands",
    "turkey",
    "australia",
    "brazil",
    "india",
    "china",
    "japan",
    "mexico",
}

CITY_TO_COUNTRY = {
    "new york": "united states",
    "los angeles": "united states",
    "chicago": "united states",
    "london": "united kingdom",
    "amsterdam": "netherlands",
    "berlin": "germany",
    "paris": "france",
    "madrid": "spain",
    "rome": "italy",
    "istanbul": "turkey",
    "ankara": "turkey",
    "sydney": "australia",
    "melbourne": "australia",
    "toronto": "canada",
}

COUNTRY_TO_REGION = {
    "united states": "NA",
    "canada": "NA",
    "mexico": "LATAM",
    "united kingdom": "EU",
    "germany": "EU",
    "france": "EU",
    "spain": "EU",
    "italy": "EU",
    "netherlands": "EU",
    "turkey": "MEA",
    "australia": "APAC",
    "india": "APAC",
    "china": "APAC",
    "japan": "APAC",
    "brazil": "LATAM",
}

REGION_CODES = {"NA", "EU", "MEA", "APAC", "LATAM"}

POSTAL_CODE_PATTERNS = {
    "united states": r"^\d{5}(\d{4})?$",
    "canada": r"^[A-Z]\d[A-Z]\d[A-Z]\d$",
    "united kingdom": r"^[A-Z]{1,2}\d[A-Z\d]?\d[A-Z]{2}$",
    "germany": r"^\d{5}$",
    "france": r"^\d{5}$",
    "spain": r"^\d{5}$",
    "italy": r"^\d{5}$",
    "netherlands": r"^\d{4}[A-Z]{2}$",
    "turkey": r"^\d{5}$",
    "australia": r"^\d{4}$",
    "india": r"^\d{6}$",
    "china": r"^\d{6}$",
    "japan": r"^\d{7}$",
    "mexico": r"^\d{5}$",
    "brazil": r"^\d{8}$",
}

POSTAL_CODE_REGEX = {
    country: re.compile(pattern, flags=re.IGNORECASE)
    for country, pattern in POSTAL_CODE_PATTERNS.items()
}

PHONE_PREFIX_COUNTRY = {
    "1": {"united states", "canada"},
    "44": {"united kingdom"},
    "49": {"germany"},
    "33": {"france"},
    "34": {"spain"},
    "39": {"italy"},
    "31": {"netherlands"},
    "90": {"turkey"},
    "61": {"australia"},
    "91": {"india"},
    "86": {"china"},
    "81": {"japan"},
    "52": {"mexico"},
    "55": {"brazil"},
}

ALLOWED_STATUSES = {
    "approved",
    "completed",
    "pending",
    "processing",
    "rejected",
}

ALLOWED_PAYMENT_METHODS = {
    "cash",
    "check",
    "credit card",
    "crypto",
    "paypal",
    "wire transfer",
}

STATUS_REQUIRES_PAYMENT = {"approved", "completed"}

ALLOWED_DEPARTMENTS = {
    "finance",
    "marketing",
    "sales",
    "support",
    "operations",
    "legal",
    "it",
    "hr",
}

ALLOWED_CATEGORIES = {
    "electronics",
    "furniture",
    "clothing",
    "hardware",
    "software",
}

NUMERIC_CLEAN_RE = re.compile(r"[^0-9.\-]")
POSTAL_CLEAN_RE = re.compile(r"[^0-9A-Za-z]")
PHONE_DIGITS_RE = re.compile(r"\D")


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


def _strip_accents(value: str) -> str:
    """Remove diacritics so reference matching remains ASCII-safe.

    Business purpose:
        Normalize values with accents to their ASCII equivalents.
    Why it exists:
        Reference data is stored in ASCII and should match accented inputs.
    Where used:
        Country/region normalization for geography rules.
    Inputs:
        value: Raw lowercase string value.
    Returns:
        ASCII-only string with diacritics removed.
    """
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )


def normalize_key(value: object) -> str:
    """Normalize values for reference lookups with accent stripping.

    Business purpose:
        Produce stable keys for reference lookups without Unicode drift.
    Why it exists:
        Some datasets contain accented variants (e.g., Turkiye).
    Where used:
        Country, region, and postal code validation rules.
    Inputs:
        value: Raw value from CSV.
    Returns:
        Normalized, ASCII-safe string.
    """
    text = normalize_value(value)
    if not text:
        return ""
    return _strip_accents(text)


def normalize_country(value: object) -> str:
    """Normalize country values to canonical keys for lookup tables.

    Business purpose:
        Align country values with reference mappings.
    Why it exists:
        Datasets may use abbreviations (USA, UK) or accented variants.
    Where used:
        Geography validation rules.
    Inputs:
        value: Raw country value.
    Returns:
        Canonical country string for lookups.
    """
    normalized = normalize_key(value)
    return COUNTRY_ALIASES.get(normalized, normalized)


def normalize_region(value: object) -> str:
    """Normalize region codes for comparison.

    Business purpose:
        Provide a consistent region code for country-region checks.
    Why it exists:
        Region codes may vary in casing or whitespace.
    Where used:
        REGION_COUNTRY_MISMATCH rule.
    Inputs:
        value: Raw region code value.
    Returns:
        Uppercase region code string.
    """
    normalized = normalize_key(value)
    return normalized.upper() if normalized else ""


def normalize_postal_code(value: object) -> str:
    """Normalize postal codes to a compact, comparable format.

    Business purpose:
        Standardize postal code values for regex validation.
    Why it exists:
        Postal codes often include spaces or hyphens.
    Where used:
        POSTAL_CODE_INVALID rule.
    Inputs:
        value: Raw postal code value.
    Returns:
        Uppercase alphanumeric postal code without separators.
    """
    normalized = normalize_key(value)
    if not normalized:
        return ""
    return POSTAL_CLEAN_RE.sub("", normalized).upper()


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


def country_city_mismatch(
    country: object,
    city: object,
    city_to_country: Mapping[str, str],
) -> RuleResult | None:
    """Flag rows where a known city belongs to a different country.

    Business purpose:
        Detect geography mismatches that break location analytics.
    Why it exists:
        City-country inconsistencies often indicate dirty data.
    Where used:
        ETL validation when building issue rows.
    Inputs:
        country: Raw country value from the row.
        city: Raw city value from the row.
        city_to_country: Mapping of known city names to countries.
    Returns:
        RuleResult when a mismatch is detected, otherwise None.
    """
    normalized_country = normalize_country(country)
    if normalized_country not in KNOWN_COUNTRIES:
        return None
    normalized_city = normalize_key(city)
    if not normalized_city:
        return None
    expected = city_to_country.get(normalized_city)
    if expected and expected != normalized_country:
        return (RULE_COUNTRY_CITY_MISMATCH, SEVERITY_ERROR)
    return None


def region_country_mismatch(country: object, region_code: object) -> RuleResult | None:
    """Flag rows where region code conflicts with the country mapping.

    Business purpose:
        Detect mismatched region assignments for geography reporting.
    Why it exists:
        Incorrect regions distort regional analytics rollups.
    Where used:
        ETL validation when building issue rows.
    Inputs:
        country: Raw country value.
        region_code: Raw region code value.
    Returns:
        RuleResult when a mismatch is detected, otherwise None.
    """
    normalized_country = normalize_country(country)
    expected = COUNTRY_TO_REGION.get(normalized_country)
    if not expected:
        return None
    normalized_region = normalize_region(region_code)
    if not normalized_region:
        return None
    if normalized_region != expected:
        return (RULE_REGION_COUNTRY_MISMATCH, SEVERITY_ERROR)
    return None


def postal_code_invalid(country: object, postal_code: object) -> RuleResult | None:
    """Validate postal code formats using country-specific patterns.

    Business purpose:
        Surface postal codes that do not match expected formats.
    Why it exists:
        Bad postal codes hurt shipping analytics and segmentation.
    Where used:
        ETL validation when building issue rows.
    Inputs:
        country: Raw country value.
        postal_code: Raw postal code value.
    Returns:
        RuleResult when a mismatch is detected, otherwise None.
    """
    normalized_country = normalize_country(country)
    pattern = POSTAL_CODE_REGEX.get(normalized_country)
    if not pattern:
        return None
    normalized_postal = normalize_postal_code(postal_code)
    if not normalized_postal:
        return None
    if not pattern.fullmatch(normalized_postal):
        return (RULE_POSTAL_CODE_INVALID, SEVERITY_WARN)
    return None


def phone_country_mismatch(country: object, phone: object) -> RuleResult | None:
    """Flag phone numbers whose country prefix conflicts with the country value.

    Business purpose:
        Detect mismatched phone prefixes against country assignments.
    Why it exists:
        Mismatched prefixes suggest inconsistent contact data.
    Where used:
        ETL validation when building issue rows.
    Inputs:
        country: Raw country value.
        phone: Raw phone number value.
    Returns:
        RuleResult when a mismatch is detected, otherwise None.
    """
    normalized_country = normalize_country(country)
    if normalized_country not in KNOWN_COUNTRIES:
        return None
    if phone is None:
        return None
    phone_text = str(phone).strip()
    if not phone_text or not (phone_text.startswith("+") or phone_text.startswith("00")):
        return None
    digits = PHONE_DIGITS_RE.sub("", phone_text)
    if digits.startswith("00"):
        digits = digits[2:]
    if not digits:
        return None
    matched_prefix = None
    for prefix in sorted(PHONE_PREFIX_COUNTRY, key=len, reverse=True):
        if digits.startswith(prefix):
            matched_prefix = prefix
            break
    if not matched_prefix:
        return None
    countries = PHONE_PREFIX_COUNTRY.get(matched_prefix, set())
    if len(countries) != 1:
        return None
    if normalized_country not in countries:
        return (RULE_PHONE_COUNTRY_MISMATCH, SEVERITY_WARN)
    return None


def status_invalid(status: object) -> RuleResult | None:
    """Flag unrecognized status values.

    Business purpose:
        Surface invalid order status values that break reporting.
    Why it exists:
        Status typos fragment lifecycle analytics.
    Where used:
        ETL validation when building issue rows.
    Inputs:
        status: Raw status value.
    Returns:
        RuleResult when invalid, otherwise None.
    """
    normalized = normalize_value(status)
    if not normalized:
        return None
    if normalized not in ALLOWED_STATUSES:
        return (RULE_STATUS_INVALID, SEVERITY_WARN)
    return None


def payment_method_invalid(payment_method: object) -> RuleResult | None:
    """Flag unrecognized payment method values.

    Business purpose:
        Surface invalid payment method values that skew analytics.
    Why it exists:
        Payment typos fragment payment breakdowns.
    Where used:
        ETL validation when building issue rows.
    Inputs:
        payment_method: Raw payment method value.
    Returns:
        RuleResult when invalid, otherwise None.
    """
    normalized = normalize_value(payment_method)
    if not normalized:
        return None
    if normalized not in ALLOWED_PAYMENT_METHODS:
        return (RULE_PAYMENT_METHOD_INVALID, SEVERITY_WARN)
    return None


def status_payment_inconsistent(status: object, payment_method: object) -> RuleResult | None:
    """Flag completed/approved statuses without a payment method.

    Business purpose:
        Highlight missing payment data on completed transactions.
    Why it exists:
        Completed orders should have a payment method recorded.
    Where used:
        ETL validation when building issue rows.
    Inputs:
        status: Raw status value.
        payment_method: Raw payment method value.
    Returns:
        RuleResult when inconsistent, otherwise None.
    """
    normalized_status = normalize_value(status)
    if normalized_status not in STATUS_REQUIRES_PAYMENT:
        return None
    normalized_payment = normalize_value(payment_method)
    if not normalized_payment:
        return (RULE_STATUS_PAYMENT_INCONSISTENT, SEVERITY_WARN)
    if normalized_payment not in ALLOWED_PAYMENT_METHODS:
        return (RULE_STATUS_PAYMENT_INCONSISTENT, SEVERITY_WARN)
    return None


def category_invalid(category: object) -> RuleResult | None:
    """Flag category values outside the canonical category set.

    Business purpose:
        Surface unexpected categories that fragment analytics.
    Why it exists:
        Category typos and unknown values reduce grouping accuracy.
    Where used:
        ETL validation when building issue rows.
    Inputs:
        category: Raw category value.
    Returns:
        RuleResult when invalid, otherwise None.
    """
    normalized = normalize_value(category)
    if not normalized:
        return None
    if normalized not in ALLOWED_CATEGORIES:
        return (RULE_CATEGORY_INVALID, SEVERITY_WARN)
    return None


def department_invalid(department: object) -> RuleResult | None:
    """Flag department values outside the canonical department set.

    Business purpose:
        Surface unexpected departments that fragment analytics.
    Why it exists:
        Department typos and unknown values reduce grouping accuracy.
    Where used:
        ETL validation when building issue rows.
    Inputs:
        department: Raw department value.
    Returns:
        RuleResult when invalid, otherwise None.
    """
    normalized = normalize_value(department)
    if not normalized:
        return None
    if normalized not in ALLOWED_DEPARTMENTS:
        return (RULE_DEPARTMENT_INVALID, SEVERITY_WARN)
    return None


def financial_total_mismatch(raw_row: Mapping[str, object]) -> RuleResult | None:
    """Detect mismatches between computed and reported totals with tolerance.

    Business purpose:
        Identify financial inconsistencies that skew revenue analytics.
    Why it exists:
        Totals should align with quantity, price, discounts, and taxes.
    Where used:
        ETL validation when building issue rows.
    Inputs:
        raw_row: Raw CSV row values keyed by column name.
    Returns:
        RuleResult when a mismatch exceeds tolerance, otherwise None.
    """
    quantity, quantity_state = _parse_numeric_with_state(raw_row.get("quantity"))
    unit_price, unit_price_state = _parse_numeric_with_state(raw_row.get("unit_price"))
    total_amount, total_state = _parse_numeric_with_state(raw_row.get("total_amount"))
    discount_percent, discount_state = _parse_numeric_with_state(raw_row.get("discount_percent"))
    tax_rate, tax_state = _parse_numeric_with_state(raw_row.get("tax_rate"))

    # Skip rule when key fields are missing or invalid to avoid false positives.
    if quantity is None or unit_price is None or total_amount is None:
        return None
    if quantity_state == "invalid" or unit_price_state == "invalid" or total_state == "invalid":
        return None
    if discount_state == "invalid" or tax_state == "invalid":
        return None

    discount_percent = discount_percent or 0.0
    tax_rate = tax_rate or 0.0
    expected_total = (quantity * unit_price) * (1 - discount_percent / 100) * (1 + tax_rate / 100)
    tolerance = max(FINANCIAL_MISMATCH_MIN_TOLERANCE, FINANCIAL_MISMATCH_REL_TOLERANCE * abs(expected_total))
    if abs(expected_total - total_amount) > tolerance:
        return (RULE_FINANCIAL_TOTAL_MISMATCH, SEVERITY_ERROR)
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


def _parse_numeric_with_state(value: object) -> tuple[float | None, str]:
    """Parse numeric inputs and return the parse state.

    Business purpose:
        Differentiate missing values from invalid numeric strings.
    Why it exists:
        Some rules treat missing as zero but skip on invalid formats.
    Where used:
        Financial total mismatch validation.
    Inputs:
        value: Raw numeric value from CSV.
    Returns:
        Tuple of (parsed float or None, state: ok/missing/invalid).
    """
    if value is None:
        return None, "missing"
    if isinstance(value, (int, float)):
        return float(value), "ok"
    text = str(value).strip()
    if not text:
        return None, "missing"
    cleaned = NUMERIC_CLEAN_RE.sub("", text)
    if not cleaned or cleaned in {"-", ".", "-."}:
        return None, "invalid"
    try:
        return float(cleaned), "ok"
    except ValueError:
        return None, "invalid"

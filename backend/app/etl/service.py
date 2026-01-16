"""ETL pipeline implementation."""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from datetime import datetime

import polars as pl
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.clickhouse import (
    CLICKHOUSE_COLUMNS,
    all_fact_table,
    ensure_clickhouse_schema,
    fact_table,
    get_clickhouse_client,
    issue_fact_table,
)
from app.db.models import EtlRun, QualityFinding, QualityReport, RoleEnum, Tenant, User
from app.etl.issues_writer import IssuesWriter
from app.etl.quality import QualityAccumulator
from app.etl.raw_writer import write_raw_batch
from app.etl import rules
from app.etl.schema import REQUIRED_TRANSACTION_COLUMNS, build_explicit_mapping

logger = logging.getLogger(__name__)

DERIVED_COLUMNS = ["product_id", "price", "user_id", "amount", "event_ts"]
TRANSFORM_COLUMNS = REQUIRED_TRANSACTION_COLUMNS + DERIVED_COLUMNS


def _clean_str(series: pl.Series) -> pl.Series:
    """Trim and coerce string columns to keep comparisons stable.

    Business purpose:
        Normalize string fields before validation and grouping.
    Why it exists:
        Raw CSV values may include inconsistent casing or whitespace.
    Where used:
        ETL transformation step for text columns.
    Inputs:
        series: Polars Series containing raw string values.
    Returns:
        Cleaned Polars Series with trimmed string values.
    """
    return series.cast(pl.Utf8, strict=False).str.strip_chars()


def _normalize_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Normalize column header whitespace without changing values.

    Business purpose:
        Ensure column names are stable for mapping and selection.
    Why it exists:
        CSV headers may include leading/trailing whitespace.
    Where used:
        ETL ingestion before explicit mapping.
    Inputs:
        df: Incoming Polars DataFrame.
    Returns:
        DataFrame with stripped column names.
    """
    return df.rename({col: col.strip() for col in df.columns})


def _ensure_columns(df: pl.DataFrame, columns: list[str]) -> pl.DataFrame:
    """Ensure expected columns exist so transforms are predictable.

    Business purpose:
        Guarantee downstream transformations can rely on a fixed schema.
    Why it exists:
        Missing optional columns should not crash the pipeline.
    Where used:
        ETL transform stage after column mapping.
    Inputs:
        df: Polars DataFrame to adjust.
        columns: Required column names to ensure.
    Returns:
        DataFrame with any missing columns added as nulls.
    """
    for col in columns:
        if col not in df.columns:
            df = df.with_columns(pl.lit(None).alias(col))
    return df


def _hash_value(value: str) -> int:
    """Compute a stable hash for deterministic owner assignment.

    Business purpose:
        Distribute row ownership across users deterministically.
    Why it exists:
        Keeps owner_user_id stable across repeated ETL runs.
    Where used:
        _pick_owner when assigning owners based on customer identifiers.
    Inputs:
        value: String value used as the hash input.
    Returns:
        Integer hash derived from SHA-256 digest.
    """
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _pick_owner(user_ids: list[int], value: str | None, default_owner: int) -> int:
    """Assign a deterministic owner for row-level scoping.

    Business purpose:
        Support per-user scoping for NORMAL users in analytics.
    Why it exists:
        Ensures ownership assignment is stable and reproducible.
    Where used:
        ETL transformation when setting owner_user_id.
    Inputs:
        user_ids: List of eligible user ids.
        value: Value used to hash into an owner choice.
        default_owner: Fallback owner when no users exist.
    Returns:
        Selected owner user id.
    """
    if not user_ids:
        return default_owner
    if value is None:
        return user_ids[0]
    # Hash to distribute ownership across available users.
    hashed = _hash_value(value)
    return user_ids[hashed % len(user_ids)]


def _count_missing(
    df: pl.DataFrame,
    accumulator: QualityAccumulator,
    columns: list[str] | None = None,
) -> None:
    """Record missing and blank counts for each column in the batch.

    Business purpose:
        Track missingness rates for quality reporting.
    Why it exists:
        Missing values can invalidate analytics and need visibility.
    Where used:
        ETL transformation pipeline.
    Inputs:
        df: Polars DataFrame for the current batch.
        accumulator: QualityAccumulator to update.
        columns: Optional list of columns to evaluate.
    Returns:
        None; updates accumulator counters.
    """
    for col in (columns or df.columns):
        series = df[col]
        null_count = int(series.is_null().sum())
        blank_count = 0
        if series.dtype == pl.Utf8:
            blank_count = int(series.str.strip_chars().eq("").sum())
        accumulator.add_missing(col, null_count + blank_count, blank_count)


def validate_transformed_dataframe(df: pl.DataFrame) -> dict[str, float]:
    """Validate transformed data and return null ratios for required columns.

    Business purpose:
        Ensure transformed data meets minimum schema requirements.
    Why it exists:
        Prevents incomplete datasets from being loaded into analytics tables.
    Where used:
        ETL pipeline after transformations and before inserts.
    Inputs:
        df: Transformed Polars DataFrame.
    Returns:
        Dict mapping required column names to null ratios.
    """
    missing = [col for col in REQUIRED_TRANSACTION_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Transformed data missing required columns: {', '.join(missing)}")

    ratios: dict[str, float] = {}
    for col in REQUIRED_TRANSACTION_COLUMNS:
        series = df[col]
        missing_count = int(series.is_null().sum())
        if series.dtype == pl.Utf8:
            missing_count += int(series.str.strip_chars().eq("").sum())
        # Ratio uses dataframe height to normalize missingness.
        ratio = (missing_count / df.height) if df.height else 0
        ratios[col] = ratio
    return ratios


def _count_parse_failures(
    raw: pl.Series,
    parsed: pl.Series,
    column: str,
    accumulator: QualityAccumulator,
) -> None:
    """Track parsing failures without rejecting the row.

    Business purpose:
        Capture parsing issues for quality reporting without failing ingestion.
    Why it exists:
        Parsing errors are common in CSV data and should be visible.
    Where used:
        ETL transformation when casting columns to numeric/datetime.
    Inputs:
        raw: Raw Polars Series before parsing.
        parsed: Parsed Polars Series after casting.
        column: Column name used for reporting.
        accumulator: QualityAccumulator to update.
    Returns:
        None; updates accumulator counters.
    """
    raw_stripped = raw.cast(pl.Utf8, strict=False).str.strip_chars()
    failures = int(
        (
            raw_stripped.is_not_null()
            & raw_stripped.ne("")
            & parsed.is_null()
        ).sum()
    )
    if failures:
        accumulator.add_parse_fail(column, failures)


def _insert_clickhouse_table(client, df: pl.DataFrame, table: str, settings: Settings) -> None:
    """Insert cleaned rows into a ClickHouse table in batches.

    Business purpose:
        Persist transformed rows while keeping memory usage bounded.
    Why it exists:
        Large datasets require batching to avoid large payloads.
    Where used:
        ETL pipeline when inserting into clean, issue, and all tables.
    Inputs:
        client: ClickHouse client for inserts.
        df: Polars DataFrame with cleaned columns.
        table: Target ClickHouse table name.
        settings: Runtime configuration containing batch size.
    Returns:
        None; performs batched inserts.
    """
    if df.is_empty():
        return
    # Select only canonical columns to avoid schema drift.
    rows = df.select(CLICKHOUSE_COLUMNS).iter_rows()
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= settings.etl_insert_batch_size:
            # Batch insert to reduce ClickHouse insert overhead.
            # Insert cleaned rows in bulk to keep ingestion throughput high.
            # Bulk inserts reduce round-trips and amortize per-row costs.
            # Column order matches CLICKHOUSE_COLUMNS for deterministic inserts.
            client.execute(
                f"INSERT INTO {table} VALUES",
                batch,
            )
            batch = []
    if batch:
        # Flush any remaining rows in the final batch.
        # Final bulk insert ensures remaining rows are persisted.
        # Bulk insert keeps per-row overhead low for tail batches.
        # Uses the same canonical column order as earlier batches.
        client.execute(f"INSERT INTO {table} VALUES", batch)


def _insert_clickhouse(client, df: pl.DataFrame, settings: Settings) -> None:
    """Insert cleaned rows into the CLEAN fact table.

    Business purpose:
        Persist validated records for primary analytics queries.
    Why it exists:
        Encapsulates clean table selection and insertion.
    Where used:
        ETL pipeline after validation.
    Inputs:
        client: ClickHouse client for inserts.
        df: Polars DataFrame of clean rows.
        settings: Runtime configuration for table names and batch size.
    Returns:
        None; inserts rows into the clean fact table.
    """
    table = fact_table(settings)
    _insert_clickhouse_table(client, df, table, settings)


def _fetch_existing_transaction_ids(
    client,
    settings: Settings,
    tenant_id: int,
    transaction_ids: set[str],
) -> set[str]:
    """Resolve duplicate transaction IDs against existing clean facts.

    Business purpose:
        Prevent duplicate transaction IDs from being re-ingested.
    Why it exists:
        Deduplication must consider both current batch and stored data.
    Where used:
        ETL validation when building issue masks.
    Inputs:
        client: ClickHouse client for queries.
        settings: Runtime configuration for table names.
        tenant_id: Tenant identifier for isolation.
        transaction_ids: Set of transaction IDs from the current batch.
    Returns:
        Set of transaction IDs that already exist in the clean table.
    """
    if not transaction_ids:
        return set()
    table = fact_table(settings)
    existing: set[str] = set()
    ids_list = list(transaction_ids)
    chunk_size = 5000
    for offset in range(0, len(ids_list), chunk_size):
        chunk = ids_list[offset : offset + chunk_size]
        # Query existing transaction_ids in chunks to avoid oversized IN lists.
        # Query checks for historical duplicates scoped to the tenant.
        # Chunked IN lists keep query planning and payload sizes reasonable.
        # Tenant filter aligns with partition/order keys for pruning.
        rows = client.execute(
            f"""
            SELECT transaction_id
            FROM {table}
            WHERE tenant_id = %(tenant_id)s AND transaction_id IN %(ids)s
            """,
            {"tenant_id": tenant_id, "ids": tuple(chunk)},
        )
        existing.update(row[0] for row in rows)
    return existing


def _build_city_country_reference(
    cities: list[str],
    countries: list[str],
    min_share: float = 0.9,
    min_count: int = 25,
) -> dict[str, str]:
    """Build a high-confidence city-to-country map from batch values.

    Business purpose:
        Provide a best-effort reference map when static mappings are incomplete.
    Why it exists:
        Geography validation should avoid false positives for ambiguous cities.
    Where used:
        ETL validation when computing COUNTRY_CITY_MISMATCH.
    Inputs:
        cities: Normalized city values for the batch.
        countries: Normalized country values for the batch.
        min_share: Minimum share required to accept a city-country mapping.
        min_count: Minimum observations required before mapping a city.
    Returns:
        Mapping of city to its dominant country when confidence is high.
    """
    city_country_counts: dict[str, dict[str, int]] = {}
    for city, country in zip(cities, countries):
        if not city or not country:
            continue
        city_counts = city_country_counts.setdefault(city, {})
        city_counts[country] = city_counts.get(country, 0) + 1

    mapping: dict[str, str] = {}
    for city, counts in city_country_counts.items():
        total = sum(counts.values())
        if total < min_count:
            continue
        best_country, best_count = max(counts.items(), key=lambda item: item[1])
        # Ensure the dominant country is unique to avoid ambiguous mappings.
        if sum(1 for count in counts.values() if count == best_count) > 1:
            continue
        if (best_count / total) >= min_share:
            mapping[city] = best_country
    return mapping


def _evaluate_issue_rows(
    df: pl.DataFrame,
    raw_df: pl.DataFrame,
    client,
    settings: Settings,
    tenant_id: int,
) -> tuple[pl.Series, list[dict[str, object]]]:
    """Evaluate quality rules for a batch and prepare issue payloads.

    Business purpose:
        Identify rows that should be routed to the ISSUE fact table.
    Why it exists:
        Encapsulates rule evaluation logic and issue row construction.
    Where used:
        ETL pipeline when splitting clean vs issue rows.
    Inputs:
        df: Transformed DataFrame for rule evaluation.
        raw_df: Raw DataFrame used to capture original values.
        client: ClickHouse client for duplicate checks.
        settings: Runtime configuration for table names.
        tenant_id: Tenant identifier for isolation.
    Returns:
        Tuple of (issue mask series, list of issue row dicts).
    """
    if df.is_empty():
        return pl.Series([], dtype=pl.Boolean), []

    # Normalize transaction IDs and compute duplicates within the batch.
    transaction_ids = (
        df["transaction_id"]
        .cast(pl.Utf8, strict=False)
        .fill_null("")
        .str.strip_chars()
        .to_list()
    )
    non_blank_ids = [tid for tid in transaction_ids if tid]
    duplicate_ids = {tid for tid, count in Counter(non_blank_ids).items() if count > 1}
    # Fetch IDs already present in ClickHouse to catch historical duplicates.
    existing_ids = _fetch_existing_transaction_ids(
        client,
        settings,
        tenant_id,
        set(non_blank_ids),
    )

    # Build missing field mask based on required fields.
    missing_exprs = [
        pl.col(field)
        .cast(pl.Utf8, strict=False)
        .str.strip_chars()
        .fill_null("")
        .eq("")
        for field in rules.REQUIRED_FIELDS
    ]
    missing_mask = df.select(pl.any_horizontal(missing_exprs)).to_series().to_list()

    # Build price mismatch mask using derived totals.
    quantity = pl.col("quantity").cast(pl.Float64, strict=False)
    unit_price = pl.col("unit_price").cast(pl.Float64, strict=False)
    total_amount = pl.col("total_amount").cast(pl.Float64, strict=False)
    discount_percent = pl.col("discount_percent").cast(pl.Float64, strict=False).fill_null(0)
    tax_rate = pl.col("tax_rate").cast(pl.Float64, strict=False).fill_null(0)
    valid_price = quantity.is_not_null() & unit_price.is_not_null() & total_amount.is_not_null()
    # Expected total includes discount and tax adjustments.
    expected_total = (quantity * unit_price) * (1 - discount_percent / 100) * (1 + tax_rate / 100)
    expected_cents = (expected_total * 100).round(0).cast(pl.Int64)
    total_cents = (total_amount * 100).round(0).cast(pl.Int64)
    mismatch_expr = (expected_cents - total_cents).abs() > rules.PRICE_MISMATCH_TOLERANCE_CENTS
    price_mismatch_mask = df.select((valid_price & mismatch_expr).fill_null(False)).to_series().to_list()

    # Duplicate mask uses both in-batch and existing IDs.
    duplicate_mask = [(tid in duplicate_ids) or (tid in existing_ids) for tid in transaction_ids]

    # Suspicious typos are computed from normalized country/city values.
    country_values = [rules.normalize_value(value) for value in df["country"].to_list()]
    city_values = [rules.normalize_value(value) for value in df["city"].to_list()]
    suspicious_countries = {
        value
        for value in set(country_values)
        if rules.suspected_typo_value(value, rules.COUNTRY_REFERENCE)
    }
    suspicious_cities = {
        value
        for value in set(city_values)
        if rules.suspected_typo_value(value, rules.CITY_REFERENCE)
    }
    suspected_typo_mask = [
        (country in suspicious_countries) or (city in suspicious_cities)
        for country, city in zip(country_values, city_values)
    ]

    normalized_countries = [rules.normalize_country(value) for value in df["country"].to_list()]
    normalized_cities = [rules.normalize_key(value) for value in df["city"].to_list()]
    normalized_regions = [rules.normalize_region(value) for value in df["region_code"].to_list()]
    normalized_postal_codes = [rules.normalize_postal_code(value) for value in df["postal_code"].to_list()]
    phone_values = df["phone"].to_list()
    normalized_statuses = [rules.normalize_value(value) for value in df["status"].to_list()]
    normalized_payments = [rules.normalize_value(value) for value in df["payment_method"].to_list()]
    normalized_categories = [rules.normalize_value(value) for value in df["category"].to_list()]
    normalized_departments = [rules.normalize_value(value) for value in df["department"].to_list()]

    dynamic_city_map = _build_city_country_reference(normalized_cities, normalized_countries)
    city_country_reference = dict(rules.CITY_TO_COUNTRY)
    for city, country in dynamic_city_map.items():
        if city not in city_country_reference:
            city_country_reference[city] = country

    country_city_mismatch_mask = []
    for city, country in zip(normalized_cities, normalized_countries):
        if not city or not country or country not in rules.KNOWN_COUNTRIES:
            country_city_mismatch_mask.append(False)
            continue
        expected = city_country_reference.get(city)
        country_city_mismatch_mask.append(bool(expected and expected != country))

    region_country_mismatch_mask = []
    for country, region in zip(normalized_countries, normalized_regions):
        expected = rules.COUNTRY_TO_REGION.get(country)
        if not expected or not region:
            region_country_mismatch_mask.append(False)
            continue
        region_country_mismatch_mask.append(region != expected)

    postal_code_invalid_mask = []
    for country, postal_code in zip(normalized_countries, normalized_postal_codes):
        pattern = rules.POSTAL_CODE_REGEX.get(country)
        if not pattern or not postal_code:
            postal_code_invalid_mask.append(False)
            continue
        postal_code_invalid_mask.append(not pattern.fullmatch(postal_code))

    phone_prefixes = sorted(rules.PHONE_PREFIX_COUNTRY, key=len, reverse=True)
    phone_country_mismatch_mask = []
    for country, phone in zip(normalized_countries, phone_values):
        if country not in rules.KNOWN_COUNTRIES or phone is None:
            phone_country_mismatch_mask.append(False)
            continue
        phone_text = str(phone).strip()
        if not phone_text or not (phone_text.startswith("+") or phone_text.startswith("00")):
            phone_country_mismatch_mask.append(False)
            continue
        digits = rules.PHONE_DIGITS_RE.sub("", phone_text)
        if digits.startswith("00"):
            digits = digits[2:]
        if not digits:
            phone_country_mismatch_mask.append(False)
            continue
        matched_prefix = None
        for prefix in phone_prefixes:
            if digits.startswith(prefix):
                matched_prefix = prefix
                break
        if not matched_prefix:
            phone_country_mismatch_mask.append(False)
            continue
        countries = rules.PHONE_PREFIX_COUNTRY.get(matched_prefix, set())
        if len(countries) != 1 or country in countries:
            phone_country_mismatch_mask.append(False)
            continue
        phone_country_mismatch_mask.append(True)

    status_invalid_mask = [
        bool(status and status not in rules.ALLOWED_STATUSES)
        for status in normalized_statuses
    ]
    payment_invalid_mask = [
        bool(payment and payment not in rules.ALLOWED_PAYMENT_METHODS)
        for payment in normalized_payments
    ]
    status_payment_inconsistent_mask = [
        status in rules.STATUS_REQUIRES_PAYMENT
        and (not payment or payment not in rules.ALLOWED_PAYMENT_METHODS)
        for status, payment in zip(normalized_statuses, normalized_payments)
    ]
    category_invalid_mask = [
        bool(category and category not in rules.ALLOWED_CATEGORIES)
        for category in normalized_categories
    ]
    department_invalid_mask = [
        bool(department and department not in rules.ALLOWED_DEPARTMENTS)
        for department in normalized_departments
    ]

    def _clean_numeric(expr: pl.Expr) -> pl.Expr:
        """Normalize numeric strings for tolerant financial validation."""
        return expr.str.replace_all(r"[^0-9.\\-]", "").cast(pl.Float64, strict=False)

    quantity_raw = pl.col("quantity").cast(pl.Utf8, strict=False).fill_null("").str.strip_chars()
    unit_price_raw = pl.col("unit_price").cast(pl.Utf8, strict=False).fill_null("").str.strip_chars()
    total_amount_raw = pl.col("total_amount").cast(pl.Utf8, strict=False).fill_null("").str.strip_chars()
    discount_raw = pl.col("discount_percent").cast(pl.Utf8, strict=False).fill_null("").str.strip_chars()
    tax_raw = pl.col("tax_rate").cast(pl.Utf8, strict=False).fill_null("").str.strip_chars()

    quantity_val = _clean_numeric(quantity_raw)
    unit_price_val = _clean_numeric(unit_price_raw)
    total_amount_val = _clean_numeric(total_amount_raw)
    discount_val = _clean_numeric(discount_raw)
    tax_val = _clean_numeric(tax_raw)

    discount_invalid = (discount_raw != "") & discount_val.is_null()
    tax_invalid = (tax_raw != "") & tax_val.is_null()
    valid_financial = (
        quantity_val.is_not_null()
        & unit_price_val.is_not_null()
        & total_amount_val.is_not_null()
        & ~discount_invalid
        & ~tax_invalid
    )
    expected_total = (
        quantity_val
        * unit_price_val
        * (1 - discount_val.fill_null(0.0) / 100)
        * (1 + tax_val.fill_null(0.0) / 100)
    )
    tolerance = pl.when(
        expected_total.abs() * rules.FINANCIAL_MISMATCH_REL_TOLERANCE
        > rules.FINANCIAL_MISMATCH_MIN_TOLERANCE
    ).then(
        expected_total.abs() * rules.FINANCIAL_MISMATCH_REL_TOLERANCE
    ).otherwise(
        rules.FINANCIAL_MISMATCH_MIN_TOLERANCE
    )
    financial_mismatch_mask = df.select(
        (valid_financial & ((expected_total - total_amount_val).abs() > tolerance)).fill_null(False)
    ).to_series().to_list()

    # Compose the final issue mask by combining all rule masks.
    issue_mask = [
        missing_mask[idx]
        or price_mismatch_mask[idx]
        or duplicate_mask[idx]
        or suspected_typo_mask[idx]
        or country_city_mismatch_mask[idx]
        or region_country_mismatch_mask[idx]
        or postal_code_invalid_mask[idx]
        or phone_country_mismatch_mask[idx]
        or financial_mismatch_mask[idx]
        or status_invalid_mask[idx]
        or payment_invalid_mask[idx]
        or status_payment_inconsistent_mask[idx]
        or category_invalid_mask[idx]
        or department_invalid_mask[idx]
        for idx in range(len(transaction_ids))
    ]
    issue_mask_series = pl.Series(issue_mask)
    if not any(issue_mask):
        return issue_mask_series, []

    # Convert raw values to strings for storage in the issues table.
    raw_strings = raw_df.with_columns(
        [
            # Map values are String; replace nulls so ClickHouse can accept them.
            pl.col(col).cast(pl.Utf8, strict=False).fill_null("").alias(col)
            for col in raw_df.columns
        ]
    )
    raw_issue_dicts = raw_strings.filter(issue_mask_series).to_dicts()

    issue_rows: list[dict[str, object]] = []
    raw_idx = 0
    detected_at = datetime.utcnow()
    for idx, flagged in enumerate(issue_mask):
        if not flagged:
            continue
        issues: list[str] = []
        severity: list[str] = []
        issue_set: set[str] = set()

        def _add_issue(code: str, level: str) -> None:
            """Append a rule code and severity once per row for deduplication."""
            if code in issue_set:
                return
            issue_set.add(code)
            issues.append(code)
            severity.append(level)

        # Build issue codes and severities in a fixed order for consistency.
        if missing_mask[idx]:
            _add_issue(rules.RULE_MISSING_REQUIRED, rules.SEVERITY_ERROR)
        if price_mismatch_mask[idx]:
            _add_issue(rules.RULE_PRICE_MISMATCH, rules.SEVERITY_ERROR)
        if duplicate_mask[idx]:
            _add_issue(rules.RULE_DUPLICATE_TRANSACTION_ID, rules.SEVERITY_ERROR)
        if suspected_typo_mask[idx]:
            _add_issue(rules.RULE_SUSPECTED_TYPO, rules.SEVERITY_INFO)
        if country_city_mismatch_mask[idx]:
            _add_issue(rules.RULE_COUNTRY_CITY_MISMATCH, rules.SEVERITY_ERROR)
        if region_country_mismatch_mask[idx]:
            _add_issue(rules.RULE_REGION_COUNTRY_MISMATCH, rules.SEVERITY_ERROR)
        if postal_code_invalid_mask[idx]:
            _add_issue(rules.RULE_POSTAL_CODE_INVALID, rules.SEVERITY_WARN)
        if phone_country_mismatch_mask[idx]:
            _add_issue(rules.RULE_PHONE_COUNTRY_MISMATCH, rules.SEVERITY_WARN)
        if financial_mismatch_mask[idx]:
            _add_issue(rules.RULE_FINANCIAL_TOTAL_MISMATCH, rules.SEVERITY_ERROR)
        if status_invalid_mask[idx]:
            _add_issue(rules.RULE_STATUS_INVALID, rules.SEVERITY_WARN)
        if payment_invalid_mask[idx]:
            _add_issue(rules.RULE_PAYMENT_METHOD_INVALID, rules.SEVERITY_WARN)
        if status_payment_inconsistent_mask[idx]:
            _add_issue(rules.RULE_STATUS_PAYMENT_INCONSISTENT, rules.SEVERITY_WARN)
        if category_invalid_mask[idx]:
            _add_issue(rules.RULE_CATEGORY_INVALID, rules.SEVERITY_WARN)
        if department_invalid_mask[idx]:
            _add_issue(rules.RULE_DEPARTMENT_INVALID, rules.SEVERITY_WARN)

        issue_rows.append(
            {
                "tenant_id": tenant_id,
                "transaction_id": transaction_ids[idx],
                "issues": issues,
                "severity": severity,
                "raw_columns": raw_issue_dicts[raw_idx],
                "detected_at": detected_at,
            }
        )
        raw_idx += 1

    return issue_mask_series, issue_rows


def _compute_outliers(client, tenant_id: int, table_name: str) -> dict[str, dict[str, float]]:
    """Compute basic outlier stats for numeric sanity checks.

    Business purpose:
        Provide IQR-based outlier metrics for quality reporting.
    Why it exists:
        Identifies extreme values that may skew analytics.
    Where used:
        Post-load quality checks after ClickHouse insertions.
    Inputs:
        client: ClickHouse client for analytics queries.
        tenant_id: Tenant identifier for isolation.
        table_name: ClickHouse fact table to query.
    Returns:
        Dict of outlier stats keyed by column name.
    """
    outliers: dict[str, dict[str, float]] = {}
    numeric_cols = ["quantity", "price", "amount"]
    for col in numeric_cols:
        # Query computes quartiles for IQR-based outlier thresholds.
        # quantileExact provides deterministic quartiles for reporting.
        # Tenant filter scopes the scan and aligns with ordering keys.
        # Single aggregate query avoids multiple passes per column.
        stats = client.execute(
            f"""
            SELECT
                quantileExact(0.25)({col}) AS q1,
                quantileExact(0.75)({col}) AS q3
            FROM {table_name}
            WHERE tenant_id = %(tenant_id)s AND {col} IS NOT NULL
            """,
            {"tenant_id": tenant_id},
        )
        q1, q3 = stats[0]
        if q1 is None or q3 is None:
            continue
        # IQR-based bounds for outlier detection.
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        # Query counts values outside computed bounds.
        # CountIf keeps the scan single-pass with minimal output.
        # Tenant filter ensures isolation and partition pruning.
        # Uses bounds computed above to avoid recomputing quantiles.
        count = client.execute(
            f"""
            SELECT countIf({col} < %(lower)s OR {col} > %(upper)s)
            FROM {table_name}
            WHERE tenant_id = %(tenant_id)s AND {col} IS NOT NULL
            """,
            {"tenant_id": tenant_id, "lower": lower, "upper": upper},
        )[0][0]
        outliers[col] = {
            "q1": float(q1),
            "q3": float(q3),
            "lower": float(lower),
            "upper": float(upper),
            "count": int(count),
        }
    return outliers


def run_etl(
    db: Session,
    settings: Settings,
    tenant: Tenant,
    csv_path: str,
    initiated_by_user_id: int | None = None,
    dry_run: bool = False,
) -> EtlRun:
    """Run the full CSV ingestion with RAW, CLEAN, and ISSUES separation.

    Business purpose:
        Ingest CSV data into raw, clean, issue, and all fact tables.
    Why it exists:
        Centralizes ETL logic with quality checks and tenant isolation.
    Where used:
        ETL API endpoint and CLI entrypoint.
    Inputs:
        db: SQLAlchemy session for ETL run tracking and report persistence.
        settings: Runtime configuration for batch sizes and ClickHouse access.
        tenant: Tenant model for scoping data.
        csv_path: Path to the CSV file to ingest.
        initiated_by_user_id: Optional user id to prefer for owner assignment.
        dry_run: When True, validate but do not write to ClickHouse.
    Returns:
        EtlRun record with final status and timestamps.
    """
    # Record the ETL run in the relational metadata store.
    run = EtlRun(tenant_id=tenant.id, status="running", started_at=datetime.utcnow())
    db.add(run)
    db.commit()
    db.refresh(run)

    # Ensure ClickHouse schema exists before any inserts.
    client = get_clickhouse_client(settings)
    ensure_clickhouse_schema(client, settings)
    issues_writer = IssuesWriter(client, settings)
    if not dry_run:
        logger.info("Clearing existing ClickHouse rows for tenant %s before reload", tenant.id)
        fact_tables = [
            fact_table(settings),
            issue_fact_table(settings),
            all_fact_table(settings),
        ]
        for table in fact_tables:
            # Clear tenant rows to keep reloads deterministic per tenant.
            # Query deletes tenant-scoped rows for a full reload.
            # ALTER DELETE with mutations_sync enforces completion before inserts.
            # Tenant filter limits mutation scope and cost.
            client.execute(
                f"ALTER TABLE {table} DELETE WHERE tenant_id = %(tenant_id)s SETTINGS mutations_sync=1",
                {"tenant_id": tenant.id},
            )

    quality = QualityAccumulator()
    seen_ids: set[str] = set()
    logged_preview = False
    final_status = "success"
    post_load_checks: dict[str, dict[str, float | str | bool]] = {}
    order_date_counts: dict[str, int] = {}
    order_date_total = 0
    order_date_all_midnight = True

    # Collect NORMAL users to assign deterministic owner_user_id values.
    user_ids = [
        user.id
        for user in db.query(User)
        .filter(User.tenant_id == tenant.id, User.role == RoleEnum.NORMAL)
        .all()
    ]
    default_owner = initiated_by_user_id or (user_ids[0] if user_ids else 0)

    try:
        # Stream CSV batches to keep memory bounded for large datasets.
        reader = pl.read_csv_batched(
            csv_path,
            batch_size=settings.etl_batch_size,
            infer_schema_length=1000,
        )
        mapping = None
        unknown_columns: list[str] = []

        while True:
            batches = reader.next_batches(1)
            if not batches:
                break
            batch = batches[0]
            if not dry_run:
                # Persist raw CSV rows before any transformations for auditability.
                write_raw_batch(client, batch, settings, tenant.id, run.id)
            raw_df = _normalize_columns(batch)
            df = raw_df
            if mapping is None:
                # Build a deterministic mapping once using CSV headers.
                mapping, unknown_columns = build_explicit_mapping(df.columns)
                logger.info("Explicit CSV mapping: %s", mapping)
                if unknown_columns:
                    logger.warning("Unmapped CSV columns: %s", unknown_columns)

            # Rename raw columns to canonical names and enforce expected schema.
            rename_map = {raw: canonical for canonical, raw in mapping.items() if raw in df.columns}
            df = df.rename(rename_map)
            df = df.select(list(rename_map.values()))
            df = _ensure_columns(df, TRANSFORM_COLUMNS)

            # Track total rows for quality ratio calculations.
            quality.add_rows(df.height)

            raw_category = df["category"] if "category" in df.columns else None
            transaction_raw = df["transaction_id"].cast(pl.Utf8, strict=False)
            if transaction_raw.is_null().all():
                # Fall back to a deterministic hash when source IDs are missing.
                row_exprs = [pl.col(col).cast(pl.Utf8, strict=False) for col in df.columns]
                transaction_expr = (
                    pl.concat_str(row_exprs, separator="|")
                    .hash(seed=0)
                    .cast(pl.Utf8)
                )
            else:
                transaction_expr = pl.col("transaction_id").cast(pl.Utf8, strict=False)

            customer_id = _clean_str(df["customer_id"])
            user_id = _clean_str(df["user_id"])
            customer_id = customer_id.fill_null(user_id).rename("customer_id")
            user_id = user_id.fill_null(customer_id).rename("user_id")

            customer_name = _clean_str(df["customer_name"]).rename("customer_name")
            email = _clean_str(df["email"]).rename("email")
            phone = _clean_str(df["phone"]).rename("phone")
            country = _clean_str(df["country"]).rename("country")
            city = _clean_str(df["city"]).rename("city")
            postal_code = _clean_str(df["postal_code"]).rename("postal_code")
            department = _clean_str(df["department"]).rename("department")
            payment_method = _clean_str(df["payment_method"]).rename("payment_method")
            status = _clean_str(df["status"]).rename("status")
            tier = _clean_str(df["tier"]).rename("tier")
            region_code = _clean_str(df["region_code"]).rename("region_code")
            sales_rep_id = _clean_str(df["sales_rep_id"]).rename("sales_rep_id")

            product_name = _clean_str(df["product_name"]).rename("product_name")
            product_code = _clean_str(df["product_code"])
            product_id = _clean_str(df["product_id"])
            product_code = product_code.fill_null(product_id).rename("product_code")
            product_id = product_id.fill_null(product_code).rename("product_id")

            quantity = df["quantity"].cast(pl.Float64, strict=False).rename("quantity")
            unit_price = df["unit_price"].cast(pl.Float64, strict=False)
            price = df["price"].cast(pl.Float64, strict=False)
            unit_price = unit_price.fill_null(price).rename("unit_price")
            price = price.fill_null(unit_price).rename("price")
            discount_percent = df["discount_percent"].cast(pl.Float64, strict=False).rename("discount_percent")
            tax_rate = df["tax_rate"].cast(pl.Float64, strict=False).rename("tax_rate")

            total_amount = df["total_amount"].cast(pl.Float64, strict=False)
            amount = df["amount"].cast(pl.Float64, strict=False)
            amount = amount.fill_null(quantity * unit_price)
            total_amount = total_amount.fill_null(amount).rename("total_amount")
            amount = amount.fill_null(total_amount).rename("amount")

            order_date = (
                df["order_date"]
                .cast(pl.Utf8, strict=False)
                .str.strptime(pl.Datetime, strict=False)
                .rename("order_date")
            )
            event_ts = (
                df["event_ts"]
                .cast(pl.Utf8, strict=False)
                .str.strptime(pl.Datetime, strict=False)
            )
            event_ts = event_ts.fill_null(order_date).rename("event_ts")
            order_date = order_date.fill_null(event_ts).rename("order_date")

            if order_date.len() > 0:
                order_date_df = pl.DataFrame({"order_date": order_date})
                non_null_count = int(order_date.len() - order_date.null_count())
                if non_null_count:
                    order_date_total += non_null_count
                    counts = (
                        order_date_df
                        .filter(pl.col("order_date").is_not_null())
                        .group_by("order_date")
                        .count()
                    )
                    for order_value, count in counts.iter_rows():
                        key = order_value.isoformat() if isinstance(order_value, datetime) else str(order_value)
                        order_date_counts[key] = order_date_counts.get(key, 0) + int(count)
                if order_date_all_midnight and non_null_count:
                    non_midnight = (
                        order_date_df.select(
                            (
                                (pl.col("order_date").dt.hour() != 0)
                                | (pl.col("order_date").dt.minute() != 0)
                                | (pl.col("order_date").dt.second() != 0)
                            ).any()
                        )
                        .to_series()[0]
                    )
                    if non_midnight:
                        order_date_all_midnight = False

            is_returning_customer = (
                df["is_returning_customer"]
                .cast(pl.Boolean, strict=False)
                .cast(pl.UInt8, strict=False)
                .rename("is_returning_customer")
            )
            loyalty_points = df["loyalty_points"].cast(pl.Float64, strict=False).rename("loyalty_points")
            rating = df["rating"].cast(pl.Float64, strict=False).rename("rating")

            _count_parse_failures(df["quantity"], quantity, "quantity", quality)
            _count_parse_failures(df["unit_price"], unit_price, "unit_price", quality)
            _count_parse_failures(df["price"], price, "price", quality)
            _count_parse_failures(df["discount_percent"], discount_percent, "discount_percent", quality)
            _count_parse_failures(df["tax_rate"], tax_rate, "tax_rate", quality)
            _count_parse_failures(df["total_amount"], total_amount, "total_amount", quality)
            _count_parse_failures(df["amount"], amount, "amount", quality)
            _count_parse_failures(df["order_date"], order_date, "order_date", quality)
            _count_parse_failures(df["event_ts"], event_ts, "event_ts", quality)

            if raw_category is not None:
                # Normalize categories and track inconsistencies.
                raw_category_str = _clean_str(raw_category)
                normalized_category = raw_category_str.str.to_lowercase()
                mismatch = (raw_category_str != normalized_category) & raw_category_str.is_not_null()
                mismatches = raw_category_str.filter(mismatch).to_list()
                for value in mismatches[:5]:
                    quality.add_category_inconsistency(str(value))
                quality.category_inconsistent_count += max(0, len(mismatches) - len(quality.category_examples))
                category = normalized_category.rename("category")
            else:
                category = pl.lit(None).alias("category")

            owner_user_id = customer_id.map_elements(
                # Deterministic owner assignment keeps per-user scoping stable across loads.
                lambda value: _pick_owner(user_ids, value, default_owner),
                return_dtype=pl.UInt32,
            ).rename("owner_user_id")

            ingestion_ts = pl.lit(datetime.utcnow()).alias("ingestion_ts")

            cleaned = (
                df.with_columns(
                    [
                        pl.lit(tenant.id).cast(pl.UInt32).alias("tenant_id"),
                        owner_user_id,
                        transaction_expr.alias("transaction_id"),
                        customer_id,
                        customer_name,
                        email,
                        phone,
                        country,
                        city,
                        postal_code,
                        department,
                        category if isinstance(category, pl.Series) else category,
                        product_name,
                        product_code,
                        product_id,
                        quantity,
                        unit_price,
                        price,
                        discount_percent,
                        tax_rate,
                        payment_method,
                        status,
                        tier,
                        order_date,
                        is_returning_customer,
                        loyalty_points,
                        rating,
                        region_code,
                        sales_rep_id,
                        total_amount,
                        user_id,
                        amount,
                        event_ts,
                        ingestion_ts,
                    ]
                )
                .select(CLICKHOUSE_COLUMNS)
            )

            # Validate transformed schema before inserting into ClickHouse.
            validate_transformed_dataframe(cleaned)
            _count_missing(cleaned, quality, columns=REQUIRED_TRANSACTION_COLUMNS)

            if not logged_preview:
                logger.info("Transformed schema: %s", cleaned.schema)
                sample_rows = cleaned.select(REQUIRED_TRANSACTION_COLUMNS).head(5).to_dicts()
                logger.info("Sample rows: %s", sample_rows)
                logged_preview = True

            # Track duplicates across batches for quality reporting.
            for tid in cleaned["transaction_id"].to_list():
                tid_str = str(tid)
                if tid_str in seen_ids:
                    quality.add_duplicate(tid_str)
                else:
                    seen_ids.add(tid_str)

            if not dry_run:
                # Evaluate rules and split clean vs issue rows.
                issue_mask, issue_rows = _evaluate_issue_rows(
                    df,
                    raw_df,
                    client,
                    settings,
                    tenant.id,
                )
                if issue_rows:
                    # Route problematic rows to ISSUES to keep CLEAN analytics trustworthy.
                    issues_writer.write(issue_rows, etl_run_id=run.id)

                issue_df = cleaned.filter(issue_mask) if len(issue_mask) else cleaned.head(0)
                clean_df = cleaned.filter(~issue_mask) if len(issue_mask) else cleaned

                # Only rows with no issues should land in the CLEAN fact table.
                _insert_clickhouse(client, clean_df, settings)

                # ISSUE table stores only rows with issues for analytics.
                issue_table = issue_fact_table(settings)
                _insert_clickhouse_table(client, issue_df, issue_table, settings)

                # ALL table stores both clean and issue rows.
                all_table = all_fact_table(settings)
                _insert_clickhouse_table(client, cleaned, all_table, settings)

            if dry_run:
                logger.info("Dry-run enabled; stopping after first batch.")
                break

        # Identify columns with extreme null ratios.
        null_heavy: dict[str, float] = {}
        for col in REQUIRED_TRANSACTION_COLUMNS:
            missing = quality.missing_counts.get(col, 0)
            ratio = (missing / quality.total_rows) if quality.total_rows else 0
            if ratio >= 0.95:
                null_heavy[col] = round(ratio, 6)
        if null_heavy:
            logger.warning("Columns with >95%% nulls after transform: %s", null_heavy)
            quality.set_null_heavy(null_heavy)
            final_status = "warning"

        if order_date_counts:
            most_common_timestamp, most_common_count = max(
                order_date_counts.items(),
                key=lambda item: item[1],
            )
            ratio = most_common_count / order_date_total if order_date_total else 0.0
            flagged = ratio >= rules.TEMPORAL_UNIFORMITY_THRESHOLD or order_date_all_midnight
            post_load_checks["temporal_uniformity"] = {
                "flagged": flagged,
                "most_common_timestamp": most_common_timestamp,
                "most_common_ratio": round(ratio, 6),
                "total_rows": float(order_date_total),
                "all_midnight": order_date_all_midnight,
                "threshold": rules.TEMPORAL_UNIFORMITY_THRESHOLD,
            }

        if not dry_run:
            # Compute outliers and post-load sanity checks in ClickHouse.
            quality.set_outliers(_compute_outliers(client, tenant.id, table))
            # Query verifies non-null ratio for customer_name after load.
            # Post-load query validates a key field with a lightweight aggregate.
            # Single scan with tenant filter keeps verification inexpensive.
            # Using countIf avoids fetching row-level data.
            verification = client.execute(
                f"""
                SELECT
                    count() AS total_rows,
                    countIf(customer_name IS NOT NULL) AS customer_name_non_null
                FROM {table}
                WHERE tenant_id = %(tenant_id)s
                """,
                {"tenant_id": tenant.id},
            )[0]
            total_rows, customer_name_non_null = verification
            customer_ratio = (
                (customer_name_non_null / total_rows) if total_rows else 0
            )
            post_load_checks["customer_name_non_null"] = {
                "total_rows": float(total_rows),
                "non_null": float(customer_name_non_null),
                "ratio": round(customer_ratio, 6),
            }
            if total_rows and customer_ratio <= 0.05:
                logger.warning(
                    "Post-load check: customer_name non-null ratio is %.2f%%",
                    customer_ratio * 100,
                )
                final_status = "warning"
        else:
            final_status = "dry_run"

        if post_load_checks:
            quality.set_post_load_checks(post_load_checks)

        # Persist the quality report and derived findings for the run.
        report = QualityReport(
            tenant_id=tenant.id,
            etl_run_id=run.id,
            summary_json=quality.summary(),
        )
        db.add(report)
        db.flush()

        for finding in quality.findings():
            db.add(
                QualityFinding(
                    report_id=report.id,
                    severity=finding["severity"],
                    column=finding.get("column"),
                    check=finding["check"],
                    message=finding["message"],
                    examples=finding.get("examples"),
                )
            )

        # Finalize the ETL run status.
        run.status = final_status
        run.finished_at = datetime.utcnow()
        db.commit()
        return run

    except Exception as exc:
        logger.exception("ETL failed")
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = datetime.utcnow()
        db.commit()
        raise

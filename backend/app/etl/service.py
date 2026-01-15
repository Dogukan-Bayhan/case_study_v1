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
    """Trim and coerce string columns to keep downstream comparisons stable."""
    return series.cast(pl.Utf8, strict=False).str.strip_chars()


def _normalize_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Normalize header whitespace without changing source values."""
    return df.rename({col: col.strip() for col in df.columns})


def _ensure_columns(df: pl.DataFrame, columns: list[str]) -> pl.DataFrame:
    """Guarantee expected columns exist so later transforms are predictable."""
    for col in columns:
        if col not in df.columns:
            df = df.with_columns(pl.lit(None).alias(col))
    return df


def _hash_value(value: str) -> int:
    """Stable hash used to distribute ownership across users."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _pick_owner(user_ids: list[int], value: str | None, default_owner: int) -> int:
    """Assign a deterministic owner for multi-tenant row-level scoping."""
    if not user_ids:
        return default_owner
    if value is None:
        return user_ids[0]
    hashed = _hash_value(value)
    return user_ids[hashed % len(user_ids)]


def _count_missing(
    df: pl.DataFrame,
    accumulator: QualityAccumulator,
    columns: list[str] | None = None,
) -> None:
    """Record missing/blank counts for each column in the batch."""
    for col in (columns or df.columns):
        series = df[col]
        null_count = int(series.is_null().sum())
        blank_count = 0
        if series.dtype == pl.Utf8:
            blank_count = int(series.str.strip_chars().eq("").sum())
        accumulator.add_missing(col, null_count + blank_count, blank_count)


def validate_transformed_dataframe(df: pl.DataFrame) -> dict[str, float]:
    """Validate transformed data and return null ratios for required columns."""
    missing = [col for col in REQUIRED_TRANSACTION_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Transformed data missing required columns: {', '.join(missing)}")

    ratios: dict[str, float] = {}
    for col in REQUIRED_TRANSACTION_COLUMNS:
        series = df[col]
        missing_count = int(series.is_null().sum())
        if series.dtype == pl.Utf8:
            missing_count += int(series.str.strip_chars().eq("").sum())
        ratio = (missing_count / df.height) if df.height else 0
        ratios[col] = ratio
    return ratios


def _count_parse_failures(
    raw: pl.Series,
    parsed: pl.Series,
    column: str,
    accumulator: QualityAccumulator,
) -> None:
    """Track parsing failures without rejecting the row."""
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
    """Insert cleaned rows in batches to keep memory bounded."""
    if df.is_empty():
        return
    rows = df.select(CLICKHOUSE_COLUMNS).iter_rows()
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= settings.etl_insert_batch_size:
            client.execute(
                f"INSERT INTO {table} VALUES",
                batch,
            )
            batch = []
    if batch:
        client.execute(f"INSERT INTO {table} VALUES", batch)


def _insert_clickhouse(client, df: pl.DataFrame, settings: Settings) -> None:
    """Insert cleaned rows into the CLEAN fact table."""
    table = fact_table(settings)
    _insert_clickhouse_table(client, df, table, settings)


def _fetch_existing_transaction_ids(
    client,
    settings: Settings,
    tenant_id: int,
    transaction_ids: set[str],
) -> set[str]:
    """Resolve duplicates against existing CLEAN rows without huge queries."""
    if not transaction_ids:
        return set()
    table = fact_table(settings)
    existing: set[str] = set()
    ids_list = list(transaction_ids)
    chunk_size = 5000
    for offset in range(0, len(ids_list), chunk_size):
        chunk = ids_list[offset : offset + chunk_size]
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


def _evaluate_issue_rows(
    df: pl.DataFrame,
    raw_df: pl.DataFrame,
    client,
    settings: Settings,
    tenant_id: int,
) -> tuple[pl.Series, list[dict[str, object]]]:
    """Evaluate rule masks for a batch and prepare issue payloads."""
    if df.is_empty():
        return pl.Series([], dtype=pl.Boolean), []

    transaction_ids = (
        df["transaction_id"]
        .cast(pl.Utf8, strict=False)
        .fill_null("")
        .str.strip_chars()
        .to_list()
    )
    non_blank_ids = [tid for tid in transaction_ids if tid]
    duplicate_ids = {tid for tid, count in Counter(non_blank_ids).items() if count > 1}
    existing_ids = _fetch_existing_transaction_ids(
        client,
        settings,
        tenant_id,
        set(non_blank_ids),
    )

    missing_exprs = [
        pl.col(field)
        .cast(pl.Utf8, strict=False)
        .str.strip_chars()
        .fill_null("")
        .eq("")
        for field in rules.REQUIRED_FIELDS
    ]
    missing_mask = df.select(pl.any_horizontal(missing_exprs)).to_series().to_list()

    quantity = pl.col("quantity").cast(pl.Float64, strict=False)
    unit_price = pl.col("unit_price").cast(pl.Float64, strict=False)
    total_amount = pl.col("total_amount").cast(pl.Float64, strict=False)
    discount_percent = pl.col("discount_percent").cast(pl.Float64, strict=False).fill_null(0)
    tax_rate = pl.col("tax_rate").cast(pl.Float64, strict=False).fill_null(0)
    valid_price = quantity.is_not_null() & unit_price.is_not_null() & total_amount.is_not_null()
    expected_total = (quantity * unit_price) * (1 - discount_percent / 100) * (1 + tax_rate / 100)
    expected_cents = (expected_total * 100).round(0).cast(pl.Int64)
    total_cents = (total_amount * 100).round(0).cast(pl.Int64)
    mismatch_expr = (expected_cents - total_cents).abs() > rules.PRICE_MISMATCH_TOLERANCE_CENTS
    price_mismatch_mask = df.select((valid_price & mismatch_expr).fill_null(False)).to_series().to_list()

    duplicate_mask = [(tid in duplicate_ids) or (tid in existing_ids) for tid in transaction_ids]

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

    issue_mask = [
        missing_mask[idx]
        or price_mismatch_mask[idx]
        or duplicate_mask[idx]
        or suspected_typo_mask[idx]
        for idx in range(len(transaction_ids))
    ]
    issue_mask_series = pl.Series(issue_mask)
    if not any(issue_mask):
        return issue_mask_series, []

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
        if missing_mask[idx]:
            issues.append(rules.RULE_MISSING_REQUIRED)
            severity.append(rules.SEVERITY_ERROR)
        if price_mismatch_mask[idx]:
            issues.append(rules.RULE_PRICE_MISMATCH)
            severity.append(rules.SEVERITY_ERROR)
        if duplicate_mask[idx]:
            issues.append(rules.RULE_DUPLICATE_TRANSACTION_ID)
            severity.append(rules.SEVERITY_ERROR)
        if suspected_typo_mask[idx]:
            issues.append(rules.RULE_SUSPECTED_TYPO)
            severity.append(rules.SEVERITY_INFO)

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
    """Compute basic outlier stats for numeric sanity checks."""
    outliers: dict[str, dict[str, float]] = {}
    numeric_cols = ["quantity", "price", "amount"]
    for col in numeric_cols:
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
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
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
    """Run the full CSV ingestion with RAW, CLEAN, and ISSUES separation."""
    run = EtlRun(tenant_id=tenant.id, status="running", started_at=datetime.utcnow())
    db.add(run)
    db.commit()
    db.refresh(run)

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
            client.execute(
                f"ALTER TABLE {table} DELETE WHERE tenant_id = %(tenant_id)s SETTINGS mutations_sync=1",
                {"tenant_id": tenant.id},
            )

    quality = QualityAccumulator()
    seen_ids: set[str] = set()
    logged_preview = False
    final_status = "success"

    user_ids = [
        user.id
        for user in db.query(User)
        .filter(User.tenant_id == tenant.id, User.role == RoleEnum.NORMAL)
        .all()
    ]
    default_owner = initiated_by_user_id or (user_ids[0] if user_ids else 0)

    try:
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
                mapping, unknown_columns = build_explicit_mapping(df.columns)
                logger.info("Explicit CSV mapping: %s", mapping)
                if unknown_columns:
                    logger.warning("Unmapped CSV columns: %s", unknown_columns)

            rename_map = {raw: canonical for canonical, raw in mapping.items() if raw in df.columns}
            df = df.rename(rename_map)
            df = df.select(list(rename_map.values()))
            df = _ensure_columns(df, TRANSFORM_COLUMNS)

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

            validate_transformed_dataframe(cleaned)
            _count_missing(cleaned, quality, columns=REQUIRED_TRANSACTION_COLUMNS)

            if not logged_preview:
                logger.info("Transformed schema: %s", cleaned.schema)
                sample_rows = cleaned.select(REQUIRED_TRANSACTION_COLUMNS).head(5).to_dicts()
                logger.info("Sample rows: %s", sample_rows)
                logged_preview = True

            for tid in cleaned["transaction_id"].to_list():
                tid_str = str(tid)
                if tid_str in seen_ids:
                    quality.add_duplicate(tid_str)
                else:
                    seen_ids.add(tid_str)

            if not dry_run:
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

        if not dry_run:
            quality.set_outliers(_compute_outliers(client, tenant.id, table))
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
            quality.set_post_load_checks(
                {
                    "customer_name_non_null": {
                        "total_rows": float(total_rows),
                        "non_null": float(customer_name_non_null),
                        "ratio": round(customer_ratio, 6),
                    }
                }
            )
            if total_rows and customer_ratio <= 0.05:
                logger.warning(
                    "Post-load check: customer_name non-null ratio is %.2f%%",
                    customer_ratio * 100,
                )
                final_status = "warning"
        else:
            final_status = "dry_run"

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

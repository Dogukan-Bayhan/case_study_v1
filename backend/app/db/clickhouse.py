"""ClickHouse client and schema management."""

from clickhouse_driver import Client
from tenacity import retry, stop_after_attempt, wait_fixed

from app.core.config import Settings

CLICKHOUSE_SCHEMA: list[tuple[str, str]] = [
    ("tenant_id", "UInt32"),
    ("owner_user_id", "UInt32"),
    ("transaction_id", "String"),
    ("customer_id", "Nullable(String)"),
    ("customer_name", "Nullable(String)"),
    ("email", "Nullable(String)"),
    ("phone", "Nullable(String)"),
    ("country", "Nullable(String)"),
    ("city", "Nullable(String)"),
    ("postal_code", "Nullable(String)"),
    ("department", "Nullable(String)"),
    ("category", "Nullable(String)"),
    ("product_name", "Nullable(String)"),
    ("product_code", "Nullable(String)"),
    ("product_id", "Nullable(String)"),
    ("quantity", "Nullable(Float64)"),
    ("unit_price", "Nullable(Float64)"),
    ("price", "Nullable(Float64)"),
    ("discount_percent", "Nullable(Float64)"),
    ("tax_rate", "Nullable(Float64)"),
    ("payment_method", "Nullable(String)"),
    ("status", "Nullable(String)"),
    ("tier", "Nullable(String)"),
    ("order_date", "Nullable(DateTime)"),
    ("is_returning_customer", "Nullable(UInt8)"),
    ("loyalty_points", "Nullable(Float64)"),
    ("rating", "Nullable(Float64)"),
    ("region_code", "Nullable(String)"),
    ("sales_rep_id", "Nullable(String)"),
    ("total_amount", "Nullable(Float64)"),
    ("user_id", "Nullable(String)"),
    ("amount", "Nullable(Float64)"),
    ("event_ts", "Nullable(DateTime)"),
    ("ingestion_ts", "DateTime"),
]

CLICKHOUSE_COLUMNS = [name for name, _ in CLICKHOUSE_SCHEMA]

# These are the columns that comes from CSV file
RAW_COLUMNS = [
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

#
RAW_METADATA_COLUMNS = ["tenant_id", "etl_run_id", "ingested_at"]
RAW_TABLE_COLUMNS = RAW_METADATA_COLUMNS + RAW_COLUMNS

ISSUES_TABLE_COLUMNS = [
    "tenant_id",
    "transaction_id",
    "issues",
    "severity",
    "raw_columns",
    "detected_at",
    "etl_run_id",
]


def get_clickhouse_client(settings: Settings) -> Client:
    """Create a ClickHouse client configured for analytics queries.

    Business purpose:
        Provide a reusable client for ClickHouse-backed analytics.
    Why it exists:
        Centralizes connection configuration and credentials.
    Where used:
        Analytics endpoints, ETL processes, and startup schema checks.
    Inputs:
        settings: Runtime configuration containing ClickHouse connection details.
    Returns:
        Configured ClickHouse Client instance.
    """
    return Client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database="default",
        settings={"use_numpy": False},
    )


def fact_table(settings: Settings) -> str:
    """Return the fully qualified CLEAN fact table name.

    Business purpose:
        Identify the primary analytics table containing validated records.
    Why it exists:
        Avoids scattering table name formatting across the codebase.
    Where used:
        Analytics query builders and services.
    Inputs:
        settings: Runtime configuration containing ClickHouse database name.
    Returns:
        Fully qualified table name for clean facts.
    """
    return f"{settings.clickhouse_database}.fact_transactions_clean"


def issue_fact_table(settings: Settings) -> str:
    """Return the fully qualified ISSUE fact table name.

    Business purpose:
        Identify the analytics table containing records with quality issues.
    Why it exists:
        Centralizes issue table naming for consistency.
    Where used:
        Analytics scope resolution and quality workflows.
    Inputs:
        settings: Runtime configuration containing ClickHouse database name.
    Returns:
        Fully qualified table name for issue facts.
    """
    return f"{settings.clickhouse_database}.fact_transactions_issue"


def all_fact_table(settings: Settings) -> str:
    """Return the fully qualified ALL fact table name.

    Business purpose:
        Identify the analytics table containing all records regardless of quality.
    Why it exists:
        Centralizes combined table naming for scope resolution.
    Where used:
        Analytics scope resolution and ad-hoc queries.
    Inputs:
        settings: Runtime configuration containing ClickHouse database name.
    Returns:
        Fully qualified table name for all facts.
    """
    return f"{settings.clickhouse_database}.fact_transactions_all"


def raw_table(settings: Settings) -> str:
    """Return the fully qualified RAW table name for audit storage.

    Business purpose:
        Identify the raw ingestion table containing unmodified CSV rows.
    Why it exists:
        Ensures raw table naming stays consistent across ETL and quality logic.
    Where used:
        ETL writers and quality overview queries.
    Inputs:
        settings: Runtime configuration containing ClickHouse database name.
    Returns:
        Fully qualified table name for raw ingestion data.
    """
    return f"{settings.clickhouse_database}.fact_transactions_raw"


def issues_table(settings: Settings) -> str:
    """Return the fully qualified ISSUES table name for rule violations.

    Business purpose:
        Identify the table that stores data quality issue records.
    Why it exists:
        Keeps quality table naming consistent across the backend.
    Where used:
        Quality APIs and ETL issue writers.
    Inputs:
        settings: Runtime configuration containing ClickHouse database name.
    Returns:
        Fully qualified table name for quality issues.
    """
    return f"{settings.clickhouse_database}.fact_transactions_issues"


def _ensure_fact_table(client: Client, settings: Settings, table_name: str) -> None:
    """Create or evolve a fact table to match the analytics schema.

    Business purpose:
        Ensure fact tables have the correct columns for analytics queries.
    Why it exists:
        ClickHouse tables may drift between deployments; this guards against schema mismatch.
    Where used:
        ensure_clickhouse_schema during application startup.
    Inputs:
        client: ClickHouse client for executing DDL.
        settings: Runtime configuration containing database name.
        table_name: Unqualified table name for the fact table.
    Returns:
        None; creates or alters tables as needed.
    """
    # Build column definitions from the canonical schema list.
    column_defs = ",\n            ".join(f"{name} {dtype}" for name, dtype in CLICKHOUSE_SCHEMA)
    # Create the table if it does not exist with the standard MergeTree layout.
    # Partitioning and ordering enable efficient time- and tenant-scoped queries.
    # DDL uses IF NOT EXISTS to remain safe across repeated startups.
    # Order keys align with tenant and time filters to aid pruning.
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {settings.clickhouse_database}.{table_name} (
            {column_defs}
        )
        ENGINE = MergeTree
        PARTITION BY toYYYYMM(coalesce(event_ts, ingestion_ts))
        ORDER BY (tenant_id, coalesce(event_ts, ingestion_ts), transaction_id)
        """
    )

    # Query existing columns to detect schema drift.
    # Reads ClickHouse metadata only; avoids scanning table data.
    # Keeps schema evolution lightweight without external migrations.
    existing = client.execute(
        """
        SELECT name
        FROM system.columns
        WHERE database = %(db)s AND table = %(table)s
        """,
        {"db": settings.clickhouse_database, "table": table_name},
    )
    existing_cols = {row[0] for row in existing}
    for name, dtype in CLICKHOUSE_SCHEMA:
        if name not in existing_cols:
            # Add missing columns without rebuilding the table.
            # DDL adds a column only when absent to stay idempotent.
            # Column-level alters avoid heavy table rewrites.
            # Keeps tables compatible with new analytics fields.
            client.execute(
                f"ALTER TABLE {settings.clickhouse_database}.{table_name} "
                f"ADD COLUMN IF NOT EXISTS {name} {dtype}"
            )


@retry(stop=stop_after_attempt(20), wait=wait_fixed(2))
def ensure_clickhouse_schema(client: Client, settings: Settings) -> None:
    """Create and evolve ClickHouse tables required for ETL and analytics.

    Business purpose:
        Guarantee analytics storage is ready before queries or ETL writes occur.
    Why it exists:
        ClickHouse DDL must run at startup to avoid runtime failures.
    Where used:
        Application startup and provisioning scripts.
    Inputs:
        client: ClickHouse client for executing DDL.
        settings: Runtime configuration containing database name.
    Returns:
        None; creates or alters ClickHouse schemas as needed.
    """
    # ------------------------------------------------------------------
    # 1. Ensure the ClickHouse database exists
    # ------------------------------------------------------------------
    # This creates the logical database (schema) that will contain all
    # analytics-related tables. Using IF NOT EXISTS makes this operation
    # safe to run multiple times.
    # DDL is idempotent to support repeated startup runs.
    # CREATE DATABASE is lightweight and does not scan table data.
    client.execute(f"CREATE DATABASE IF NOT EXISTS {settings.clickhouse_database}")

    # ------------------------------------------------------------------
    # 2. Ensure analytics-ready fact tables exist
    # ------------------------------------------------------------------
    _ensure_fact_table(client, settings, "fact_transactions_clean")
    _ensure_fact_table(client, settings, "fact_transactions_issue")
    _ensure_fact_table(client, settings, "fact_transactions_all")

    
    
    # ------------------------------------------------------------------
    # 3. Create the RAW table (exact CSV ingestion, no transformations)
    # ------------------------------------------------------------------
    # This table stores all incoming CSV values as strings, exactly as received.
    # It serves as an immutable audit log and allows reprocessing if ETL rules change.
    raw_cols = ",\n            ".join(f"{name} String" for name in RAW_COLUMNS)
    raw_table_name = raw_table(settings)
    # DDL defines a MergeTree with time partitioning for ingestion-time pruning.
    # Raw table stores strings to preserve source values for auditability.
    # Order keys keep tenant/etl_run lookups efficient.
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {raw_table_name} (
            tenant_id UInt32,
            etl_run_id UInt32,
            ingested_at DateTime,
            {raw_cols}
        )
        ENGINE = MergeTree
        PARTITION BY toYYYYMM(ingested_at)
        ORDER BY (tenant_id, etl_run_id, transaction_id)
        """
    )
    
    # ------------------------------------------------------------------
    # 4. Create the ISSUES table (data quality violations)
    # ------------------------------------------------------------------
    # This table captures rows that fail validation or quality checks.
    # Each row may contain multiple issues with different severities,
    # along with the original raw values that caused the failure.
    issues_table_name = issues_table(settings)
    # DDL uses MergeTree with detected_at partitioning for time-bound queries.
    # Order keys favor tenant + transaction lookups in the quality UI.
    # IF NOT EXISTS keeps the DDL idempotent across deployments.
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {issues_table_name} (
            tenant_id UInt32,
            transaction_id String,
            issues Array(String),
            severity Array(String),
            raw_columns Map(String, String),
            detected_at DateTime,
            etl_run_id UInt32
        )
        ENGINE = MergeTree
        PARTITION BY toYYYYMM(detected_at)
        ORDER BY (tenant_id, transaction_id, detected_at)
        """
    )

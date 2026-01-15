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
    """Build a ClickHouse client using runtime settings for analytics workloads."""
    return Client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database="default",
        settings={"use_numpy": False},
    )


def fact_table(settings: Settings) -> str:
    """Return the fully qualified CLEAN fact table name."""
    return f"{settings.clickhouse_database}.fact_transactions_clean"


def raw_table(settings: Settings) -> str:
    """Return the fully qualified RAW table name for audit storage."""
    return f"{settings.clickhouse_database}.fact_transactions_raw"


def issues_table(settings: Settings) -> str:
    """Return the fully qualified ISSUES table name for rule violations."""
    return f"{settings.clickhouse_database}.fact_transactions_issues"


@retry(stop=stop_after_attempt(20), wait=wait_fixed(2))
def ensure_clickhouse_schema(client: Client, settings: Settings) -> None:
    """Create and evolve ClickHouse tables required for ETL and analytics."""
    
    # ------------------------------------------------------------------
    # 1. Ensure the ClickHouse database exists
    # ------------------------------------------------------------------
    # This creates the logical database (schema) that will contain all
    # analytics-related tables. Using IF NOT EXISTS makes this operation
    # safe to run multiple times.
    client.execute(f"CREATE DATABASE IF NOT EXISTS {settings.clickhouse_database}")
    table = fact_table(settings)
    
    # Generate column definitions from the central schema contract.
    # This ensures the SQL schema stays in sync with the application code.
    column_defs = ",\n            ".join(f"{name} {dtype}" for name, dtype in CLICKHOUSE_SCHEMA)
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            {column_defs}
        )
        ENGINE = MergeTree
        PARTITION BY toYYYYMM(coalesce(event_ts, ingestion_ts))
        ORDER BY (tenant_id, coalesce(event_ts, ingestion_ts), transaction_id)
        """
    )

    # ------------------------------------------------------------------
    # 3. Handle schema drift by adding missing columns
    # ------------------------------------------------------------------
    # ClickHouse does not have a migration tool like Alembic.
    # Instead, we query the system catalog to detect missing columns
    # and add them automatically. This allows the schema to evolve
    # safely over time without breaking existing deployments.
    existing = client.execute(
        """
        SELECT name
        FROM system.columns
        WHERE database = %(db)s AND table = %(table)s
        """,
        {
            "db": settings.clickhouse_database,
            "table": "fact_transactions_clean",
        },
    )

    existing_cols = {row[0] for row in existing}

    for name, dtype in CLICKHOUSE_SCHEMA:
        if name not in existing_cols:
            client.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {dtype}")

    
    
    # ------------------------------------------------------------------
    # 4. Create the RAW table (exact CSV ingestion, no transformations)
    # ------------------------------------------------------------------
    # This table stores all incoming CSV values as strings, exactly as received.
    # It serves as an immutable audit log and allows reprocessing if ETL rules change.
    raw_cols = ",\n            ".join(f"{name} String" for name in RAW_COLUMNS)
    raw_table_name = raw_table(settings)
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
    # 5. Create the ISSUES table (data quality violations)
    # ------------------------------------------------------------------
    # This table captures rows that fail validation or quality checks.
    # Each row may contain multiple issues with different severities,
    # along with the original raw values that caused the failure.
    issues_table_name = issues_table(settings)
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

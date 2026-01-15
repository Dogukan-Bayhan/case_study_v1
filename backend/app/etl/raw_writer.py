"""Write raw CSV batches into ClickHouse for audit and reprocessing."""

from __future__ import annotations

from datetime import datetime

import polars as pl

from app.core.config import Settings
from app.db.clickhouse import RAW_COLUMNS, RAW_TABLE_COLUMNS, raw_table


def write_raw_batch(
    client,
    df: pl.DataFrame,
    settings: Settings,
    tenant_id: int,
    etl_run_id: int,
    ingested_at: datetime | None = None,
) -> None:
    """Insert raw CSV rows into the raw audit table.

    Business purpose:
        Persist unmodified input rows for audit and reprocessing.
    Why it exists:
        Raw data retention enables debugging and future ETL replays.
    Where used:
        ETL ingestion pipeline before quality rules are applied.
    Inputs:
        client: ClickHouse client for inserts.
        df: Incoming Polars DataFrame of raw CSV rows.
        settings: Runtime configuration containing batch sizes.
        tenant_id: Tenant identifier for isolation.
        etl_run_id: Identifier for the current ETL run.
        ingested_at: Optional timestamp for ingestion time.
    Returns:
        None; writes rows to ClickHouse.
    """
    if df.is_empty():
        return

    # Default to current time for ingestion timestamp.
    ingested_at = ingested_at or datetime.utcnow()
    raw_df = df

    # Ensure all required raw columns exist, filling missing with nulls.
    for col in RAW_COLUMNS:
        if col not in raw_df.columns:
            raw_df = raw_df.with_columns(pl.lit(None).alias(col))

    # Cast all fields to strings; ClickHouse raw table stores String values.
    raw_df = raw_df.with_columns(
        [
            pl.lit(tenant_id).cast(pl.UInt32).alias("tenant_id"),
            pl.lit(etl_run_id).cast(pl.UInt32).alias("etl_run_id"),
            pl.lit(ingested_at).cast(pl.Datetime).alias("ingested_at"),
        ]
        + [
            # ClickHouse String columns reject nulls; keep CSV blanks as empty strings.
            pl.col(col).cast(pl.Utf8, strict=False).fill_null("").alias(col)
            for col in RAW_COLUMNS
        ]
    ).select(RAW_TABLE_COLUMNS)

    rows = raw_df.iter_rows()
    batch = []
    batch_size = max(1, settings.etl_insert_batch_size)
    table = raw_table(settings)

    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            # Batch inserts reduce overhead for large ingestion runs.
            # Insert raw audit rows in bulk to minimize per-row latency.
            # Batched INSERT keeps ClickHouse payload sizes bounded.
            # Raw table stores string values exactly as ingested.
            client.execute(f"INSERT INTO {table} VALUES", batch)
            batch = []

    if batch:
        # Insert any remaining rows in the final batch.
        # Final batch insert flushes the remainder of the raw audit rows.
        # Bulk insert avoids per-row driver overhead.
        # Raw table schema is append-only for auditability.
        client.execute(f"INSERT INTO {table} VALUES", batch)

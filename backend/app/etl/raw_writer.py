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
    """Insert raw CSV rows without transformations (string-typed for fidelity)."""
    if df.is_empty():
        return

    ingested_at = ingested_at or datetime.utcnow()
    raw_df = df

    for col in RAW_COLUMNS:
        if col not in raw_df.columns:
            raw_df = raw_df.with_columns(pl.lit(None).alias(col))

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
            client.execute(f"INSERT INTO {table} VALUES", batch)
            batch = []

    if batch:
        client.execute(f"INSERT INTO {table} VALUES", batch)

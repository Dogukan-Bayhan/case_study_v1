"""Write issue rows into ClickHouse without applying validation logic."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from app.core.config import Settings
from app.db.clickhouse import ISSUES_TABLE_COLUMNS, issues_table


class IssuesWriter:
    """Idempotent writer for issues per etl_run_id (clears once per run)."""

    def __init__(self, client, settings: Settings) -> None:
        """Initialize the issues writer for a ClickHouse client.

        Business purpose:
            Provide a reusable writer for ETL issue rows.
        Why it exists:
            Ensures issue inserts are idempotent per ETL run.
        Where used:
            ETL pipeline when persisting quality issues.
        Inputs:
            client: ClickHouse client used for DDL/DML.
            settings: Runtime configuration with batch sizing.
        Returns:
            None; stores client, settings, and cleared run tracking.
        """
        self._client = client
        self._settings = settings
        self._cleared_run_ids: set[int] = set()

    def _reset_run(self, etl_run_id: int) -> None:
        """Clear prior issue rows for the same run to avoid duplicates.

        Business purpose:
            Ensure retries do not duplicate issue records.
        Why it exists:
            ETL jobs may be retried; issues must be idempotent by run id.
        Where used:
            IssuesWriter.write before inserting new rows.
        Inputs:
            etl_run_id: Identifier for the current ETL run.
        Returns:
            None; deletes existing rows for the run.
        """
        table = issues_table(self._settings)
        # Mutation is synchronized to ensure cleanup completes before inserts.
        # Query deletes prior issues for the same ETL run to keep inserts idempotent.
        # ALTER TABLE ... DELETE is scoped by etl_run_id to minimize mutation work.
        # mutations_sync=1 blocks until cleanup finishes to avoid duplicate rows.
        self._client.execute(
            f"""
            ALTER TABLE {table}
            DELETE WHERE etl_run_id = %(etl_run_id)s
            SETTINGS mutations_sync=1
            """,
            {"etl_run_id": etl_run_id},
        )

    def write(
        self,
        issue_rows: Iterable[dict[str, object]],
        *,
        etl_run_id: int,
        detected_at: datetime | None = None,
    ) -> None:
        """Insert issue rows that already contain computed arrays.

        Business purpose:
            Persist quality issues produced by ETL rules.
        Why it exists:
            Separates issue persistence from rule evaluation.
        Where used:
            ETL pipeline after validation and issue assembly.
        Inputs:
            issue_rows: Iterable of issue row dicts.
            etl_run_id: Identifier for the current ETL run.
            detected_at: Optional timestamp for detection time.
        Returns:
            None; writes rows to ClickHouse.
        """
        iterator = iter(issue_rows)
        first_row = next(iterator, None)
        if first_row is None:
            return

        if etl_run_id not in self._cleared_run_ids:
            # Run-level cleanup keeps ISSUES idempotent when ETL retries.
            self._reset_run(etl_run_id)
            self._cleared_run_ids.add(etl_run_id)

        # Default detection time to now for consistent timestamps.
        detected_at = detected_at or datetime.utcnow()
        table = issues_table(self._settings)
        batch = []
        batch_size = max(1, self._settings.etl_insert_batch_size)

        def _append_row(row: dict[str, object]) -> None:
            """Normalize a single issue row into the ClickHouse insert shape.

            Business purpose:
                Convert dict-based issue rows into tuple payloads for inserts.
            Why it exists:
                ClickHouse driver expects ordered tuples matching columns.
            Where used:
                IssuesWriter.write when batching inserts.
            Inputs:
                row: Issue row dictionary with raw_columns and metadata.
            Returns:
                None; appends a normalized tuple to the batch list.
            """
            raw_columns = {
                str(key): "" if value is None else str(value)
                for key, value in (row.get("raw_columns") or {}).items()
            }
            batch.append(
                (
                    row["tenant_id"],
                    row["transaction_id"],
                    row["issues"],
                    row["severity"],
                    raw_columns,
                    row.get("detected_at", detected_at),
                    etl_run_id,
                )
            )

        _append_row(first_row)
        for row in iterator:
            _append_row(row)
            if len(batch) >= batch_size:
                # Batch inserts reduce ClickHouse overhead for large runs.
                # Insert issue rows in bulk to reduce driver round-trips.
                # Column list ensures deterministic column order for the driver.
                # Bulk inserts keep per-row overhead low during ETL.
                self._client.execute(
                    f"INSERT INTO {table} ({', '.join(ISSUES_TABLE_COLUMNS)}) VALUES",
                    batch,
                )
                batch = []

        if batch:
            # Flush any remaining rows at the end of the stream.
            # Final batch insert flushes remaining issue rows for the run.
            # Bulk insert keeps insert overhead low for small tail batches.
            # Column list aligns with ISSUES schema ordering.
            self._client.execute(
                f"INSERT INTO {table} ({', '.join(ISSUES_TABLE_COLUMNS)}) VALUES",
                batch,
            )

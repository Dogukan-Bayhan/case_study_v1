"""Write issue rows into ClickHouse without applying validation logic."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from app.core.config import Settings
from app.db.clickhouse import ISSUES_TABLE_COLUMNS, issues_table


class IssuesWriter:
    """Idempotent writer for issues per etl_run_id (clears once per run)."""

    def __init__(self, client, settings: Settings) -> None:
        """Bind a ClickHouse client and remember cleared runs for idempotency."""
        self._client = client
        self._settings = settings
        self._cleared_run_ids: set[int] = set()

    def _reset_run(self, etl_run_id: int) -> None:
        """Clear prior issue rows for the same run to avoid duplicates."""
        table = issues_table(self._settings)
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
        """Insert rows that already contain computed issues/severity arrays."""
        iterator = iter(issue_rows)
        first_row = next(iterator, None)
        if first_row is None:
            return

        if etl_run_id not in self._cleared_run_ids:
            # Run-level cleanup keeps ISSUES idempotent when ETL retries.
            self._reset_run(etl_run_id)
            self._cleared_run_ids.add(etl_run_id)

        detected_at = detected_at or datetime.utcnow()
        table = issues_table(self._settings)
        batch = []
        batch_size = max(1, self._settings.etl_insert_batch_size)

        def _append_row(row: dict[str, object]) -> None:
            """Normalize a single issue row into the ClickHouse insert shape."""
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
                self._client.execute(
                    f"INSERT INTO {table} ({', '.join(ISSUES_TABLE_COLUMNS)}) VALUES",
                    batch,
                )
                batch = []

        if batch:
            self._client.execute(
                f"INSERT INTO {table} ({', '.join(ISSUES_TABLE_COLUMNS)}) VALUES",
                batch,
            )

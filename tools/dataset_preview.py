#!/usr/bin/env python3
"""Preview a large CSV and print basic profiling info.

    Usage:
        python dataset_preview.py --csv C:\\path\\to\\large_dataset.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import Any

MISSING_SENTINELS = {"", "null", "none", "na", "n/a"}


def _default_csv_path() -> str:
    """Resolve a sensible default CSV path for local inspection runs."""
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "..", "case_study", "data", "large_dataset.csv"),
        os.path.join(base, "..", "case_study", "large_dataset.csv"),
    ]
    for candidate in candidates:
        full = os.path.abspath(candidate)
        if os.path.exists(full):
            return full
    return os.path.abspath(candidates[0])


def _is_missing(value: str) -> bool:
    """Normalize missing-value markers so stats align with ETL behavior."""
    return value.strip().lower() in MISSING_SENTINELS


def _ensure_header(header: list[str], row: list[str], stats: dict[str, Any]) -> None:
    """Extend header/stats when CSV rows contain extra columns."""
    if len(row) <= len(header):
        return
    start = len(header)
    for idx in range(start, len(row)):
        header.append(f"extra_{idx - start + 1}")
        stats["missing"].append(0)
        stats["unique"].append(set())
        stats["numeric"].append({"count": 0, "sum": 0.0, "min": None, "max": None})


def _update_stats(row: list[str], stats: dict[str, Any]) -> None:
    """Track missing, unique, and numeric summaries for a sampled row."""
    for idx, raw in enumerate(row):
        value = raw.strip()
        if _is_missing(value):
            stats["missing"][idx] += 1
            continue

        stats["unique"][idx].add(value)
        try:
            number = float(value)
        except ValueError:
            continue
        numeric = stats["numeric"][idx]
        numeric["count"] += 1
        numeric["sum"] += number
        numeric["min"] = number if numeric["min"] is None else min(numeric["min"], number)
        numeric["max"] = number if numeric["max"] is None else max(numeric["max"], number)


def _truncate(value: str, width: int) -> str:
    """Keep console output readable without losing column alignment."""
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."


def _render_table(header: list[str], rows: list[list[str]], max_width: int) -> None:
    """Pretty-print a sample table with bounded column widths."""
    if not header:
        print("(no header)")
        return
    widths = [min(len(col), max_width) for col in header]
    for row in rows:
        for idx, raw in enumerate(row):
            widths[idx] = min(max(widths[idx], len(raw)), max_width)

    def fmt_row(values: list[str]) -> str:
        """Render a row with fixed widths to keep the preview aligned."""
        return " | ".join(_truncate(values[idx], widths[idx]).ljust(widths[idx]) for idx in range(len(widths)))

    sep = "-+-".join("-" * width for width in widths)
    print(fmt_row(header))
    print(sep)
    for row in rows:
        padded = row + [""] * (len(header) - len(row))
        print(fmt_row(padded))


def main() -> int:
    """CLI entrypoint for sampling a large CSV without loading it fully."""
    parser = argparse.ArgumentParser(description="Preview a CSV and print basic stats.")
    parser.add_argument("--csv", dest="csv_path", default=None, help="Path to CSV file")
    parser.add_argument("--delimiter", default=",", help="CSV delimiter (default: ,)")
    parser.add_argument("--encoding", default="utf-8-sig", help="File encoding")
    parser.add_argument("--max-cell-width", type=int, default=30, help="Max column width in output")
    parser.add_argument("--sample-size", type=int, default=100, help="Rows to sample for stats")
    args = parser.parse_args()

    csv_path = args.csv_path or _default_csv_path()
    csv_path = os.path.abspath(csv_path)

    if not os.path.exists(csv_path):
        print(f"CSV not found: {csv_path}")
        return 1

    size_bytes = os.path.getsize(csv_path)
    size_mb = size_bytes / (1024 * 1024)

    header: list[str] = []
    sample_rows: list[list[str]] = []
    total_rows = 0
    stats = {
        "missing": [],
        "unique": [],
        "numeric": [],
    }

    with open(csv_path, newline="", encoding=args.encoding) as handle:
        reader = csv.reader(handle, delimiter=args.delimiter)
        header = next(reader, [])
        stats["missing"] = [0] * len(header)
        stats["unique"] = [set() for _ in header]
        stats["numeric"] = [
            {"count": 0, "sum": 0.0, "min": None, "max": None} for _ in header
        ]

        for row in reader:
            total_rows += 1
            _ensure_header(header, row, stats)
            if len(sample_rows) < args.sample_size:
                padded = row + [""] * (len(header) - len(row))
                sample_rows.append(padded)
                _update_stats(padded, stats)

    print("CSV Preview")
    print("===========")
    print(f"Path: {csv_path}")
    print(f"Size: {size_mb:.2f} MB")
    print(f"Total rows (excluding header): {total_rows}")
    print(f"Columns ({len(header)}): {', '.join(header)}")
    print()

    sample_size = min(len(sample_rows), args.sample_size)
    print(f"Sample stats (first {sample_size} rows)")
    print("---------------------------------")
    for idx, col in enumerate(header):
        missing = stats["missing"][idx]
        unique_count = len(stats["unique"][idx])
        ratio = (missing / sample_size) if sample_size else 0
        numeric = stats["numeric"][idx]
        if numeric["count"] > 0:
            avg = numeric["sum"] / numeric["count"]
            numeric_info = f"num_count={numeric['count']}, min={numeric['min']}, max={numeric['max']}, avg={avg:.2f}"
        else:
            numeric_info = "num_count=0"
        print(
            f"- {col}: missing={missing} ({ratio:.1%}), unique={unique_count}, {numeric_info}"
        )
    print()

    first_10 = sample_rows[:10]
    print("First 10 rows")
    print("-------------")
    _render_table(header, first_10, args.max_cell_width)
    print()

    print(f"First {sample_size} rows")
    print("-----------------")
    _render_table(header, sample_rows[:sample_size], args.max_cell_width)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

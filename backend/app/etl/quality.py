"""Data quality accumulator and findings generator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QualityAccumulator:
    """Collect row-level quality stats and derive summary findings for reporting."""
    total_rows: int = 0
    missing_counts: dict[str, int] = field(default_factory=dict)
    blank_counts: dict[str, int] = field(default_factory=dict)
    parse_fail_counts: dict[str, int] = field(default_factory=dict)
    duplicates_count: int = 0
    duplicate_examples: list[str] = field(default_factory=list)
    category_inconsistent_count: int = 0
    category_examples: list[str] = field(default_factory=list)
    outlier_info: dict[str, dict[str, float]] = field(default_factory=dict)
    null_heavy: dict[str, float] = field(default_factory=dict)
    post_load_checks: dict[str, dict[str, float]] = field(default_factory=dict)

    def add_rows(self, count: int) -> None:
        """Increment the total row count for ratio calculations.

        Business purpose:
            Track total processed rows to compute ratios in the summary.
        Why it exists:
            Many quality metrics are ratios that require a denominator.
        Where used:
            ETL pipeline during row processing.
        Inputs:
            count: Number of rows processed in the current batch.
        Returns:
            None; updates the accumulator state.
        """
        self.total_rows += count

    def add_missing(self, column: str, missing: int, blank: int) -> None:
        """Accumulate missing and blank counts for a column.

        Business purpose:
            Track missingness to surface data completeness issues.
        Why it exists:
            Missing values can invalidate analytics and require alerting.
        Where used:
            ETL parsing and validation steps.
        Inputs:
            column: Column name being tracked.
            missing: Count of missing values.
            blank: Count of blank/whitespace values.
        Returns:
            None; updates internal counters.
        """
        self.missing_counts[column] = self.missing_counts.get(column, 0) + missing
        self.blank_counts[column] = self.blank_counts.get(column, 0) + blank

    def add_parse_fail(self, column: str, count: int) -> None:
        """Record parse failures for a column.

        Business purpose:
            Surface data type issues that may affect downstream analytics.
        Why it exists:
            Parsing failures are common in messy CSV inputs.
        Where used:
            ETL parsing when casting values to types.
        Inputs:
            column: Column name being tracked.
            count: Number of parse failures.
        Returns:
            None; updates internal counters.
        """
        self.parse_fail_counts[column] = self.parse_fail_counts.get(column, 0) + count

    def add_duplicate(self, value: str | None) -> None:
        """Count duplicate identifiers and capture examples.

        Business purpose:
            Track duplicate transaction IDs that can inflate metrics.
        Why it exists:
            Duplicate detection needs both counts and examples for reporting.
        Where used:
            ETL validation when detecting duplicate transaction IDs.
        Inputs:
            value: Duplicate identifier to record as an example.
        Returns:
            None; updates duplicate counts and example list.
        """
        self.duplicates_count += 1
        if value and len(self.duplicate_examples) < 5:
            self.duplicate_examples.append(value)

    def add_category_inconsistency(self, raw_value: str | None) -> None:
        """Track category normalization inconsistencies.

        Business purpose:
            Surface category values that require normalization.
        Why it exists:
            Inconsistent categories can fragment analytics grouping.
        Where used:
            ETL normalization step when category values are inspected.
        Inputs:
            raw_value: Raw category value that appears inconsistent.
        Returns:
            None; updates counts and example list.
        """
        self.category_inconsistent_count += 1
        if raw_value and len(self.category_examples) < 5:
            self.category_examples.append(raw_value)

    def set_outliers(self, info: dict[str, dict[str, float]]) -> None:
        """Attach outlier summaries computed after load.

        Business purpose:
            Persist post-load outlier metrics for reporting.
        Why it exists:
            Outlier analysis is performed after data is loaded.
        Where used:
            Post-load quality checks.
        Inputs:
            info: Outlier statistics keyed by column name.
        Returns:
            None; updates outlier_info state.
        """
        self.outlier_info = info

    def set_null_heavy(self, columns: dict[str, float]) -> None:
        """Capture columns with extreme null ratios.

        Business purpose:
            Highlight columns that are mostly null after transformation.
        Why it exists:
            High null ratios can indicate upstream data quality issues.
        Where used:
            Post-load checks after ETL transformation.
        Inputs:
            columns: Map of column names to null ratios.
        Returns:
            None; updates null_heavy state.
        """
        self.null_heavy = columns

    def set_post_load_checks(self, checks: dict[str, dict[str, float]]) -> None:
        """Store sanity checks computed after the ClickHouse load.

        Business purpose:
            Persist post-load checks for reporting and alerting.
        Why it exists:
            Some quality checks are only possible after data is loaded.
        Where used:
            ETL pipeline after ClickHouse insertion.
        Inputs:
            checks: Map of check names to their summary statistics.
        Returns:
            None; updates post_load_checks state.
        """
        self.post_load_checks = checks

    def summary(self) -> dict[str, Any]:
        """Build a condensed summary used by the quality report API.

        Business purpose:
            Provide a compact summary for the quality report payload.
        Why it exists:
            Downstream APIs need structured metrics rather than raw counters.
        Where used:
            Quality report creation and API responses.
        Inputs:
            None; uses accumulated counters.
        Returns:
            Dict containing summary stats for quality reporting.
        """
        missing_summary = {}
        for col, missing in self.missing_counts.items():
            # Ratio uses total_rows to normalize missingness per column.
            ratio = (missing / self.total_rows) if self.total_rows else 0
            missing_summary[col] = {
                "missing": missing,
                "blank": self.blank_counts.get(col, 0),
                "ratio": round(ratio, 6),
            }
        parse_summary = {
            col: {"count": count, "ratio": round(count / self.total_rows, 6)}
            for col, count in self.parse_fail_counts.items()
        }
        # Assemble the summary payload consumed by quality APIs.
        return {
            "total_rows": self.total_rows,
            "missing": missing_summary,
            "parse_failures": parse_summary,
            "duplicates": {
                "count": self.duplicates_count,
                "examples": self.duplicate_examples,
            },
            "category_inconsistencies": {
                "count": self.category_inconsistent_count,
                "examples": self.category_examples,
            },
            "outliers": self.outlier_info,
            "null_heavy": self.null_heavy,
            "post_load_checks": self.post_load_checks,
        }

    def findings(self) -> list[dict[str, Any]]:
        """Translate summary stats into a list of human-readable findings.

        Business purpose:
            Generate actionable findings for the quality dashboard.
        Why it exists:
            Converts raw counts into severity-ranked insights.
        Where used:
            Quality report generation and API responses.
        Inputs:
            None; uses internal summary statistics.
        Returns:
            List of finding dicts with severity and examples.
        """
        findings: list[dict[str, Any]] = []
        for col, stats in self.summary()["missing"].items():
            ratio = stats["ratio"]
            # Severity thresholds escalate with missing ratio.
            if ratio >= 0.2:
                severity = "error"
            elif ratio >= 0.05:
                severity = "warn"
            else:
                severity = "info"
            findings.append(
                {
                    "severity": severity,
                    "column": col,
                    "check": "missing_ratio",
                    "message": f"Missing or blank ratio is {ratio:.2%}",
                    "examples": {"missing": stats["missing"], "blank": stats["blank"]},
                }
            )

        for col, stats in self.summary()["parse_failures"].items():
            if stats["count"] > 0:
                # Parsing failures are warnings as they indicate type issues.
                findings.append(
                    {
                        "severity": "warn",
                        "column": col,
                        "check": "parse_failure",
                        "message": f"Parse failures for {col}: {stats['count']}",
                        "examples": None,
                    }
                )

        if self.duplicates_count > 0:
            findings.append(
                {
                    "severity": "warn",
                    "column": "transaction_id",
                    "check": "duplicates",
                    "message": f"Found {self.duplicates_count} duplicate transactions",
                    "examples": {"examples": self.duplicate_examples},
                }
            )

        if self.category_inconsistent_count > 0:
            findings.append(
                {
                    "severity": "info",
                    "column": "category",
                    "check": "category_normalization",
                    "message": "Category values require normalization",
                    "examples": {"examples": self.category_examples},
                }
            )

        for col, stats in self.outlier_info.items():
            # Outliers are informational for analyst review.
            findings.append(
                {
                    "severity": "info",
                    "column": col,
                    "check": "outliers",
                    "message": f"Outliers detected for {col}",
                    "examples": stats,
                }
            )

        for col, ratio in self.null_heavy.items():
            # Null-heavy columns indicate transformation or ingestion issues.
            findings.append(
                {
                    "severity": "warn",
                    "column": col,
                    "check": "null_heavy",
                    "message": f"{col} is {ratio:.2%} null after transformation",
                    "examples": {"ratio": ratio},
                }
            )

        return findings

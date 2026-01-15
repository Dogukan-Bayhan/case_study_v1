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
        """Track the total number of processed rows for ratio calculations."""
        self.total_rows += count

    def add_missing(self, column: str, missing: int, blank: int) -> None:
        """Accumulate missing/blank counts per column."""
        self.missing_counts[column] = self.missing_counts.get(column, 0) + missing
        self.blank_counts[column] = self.blank_counts.get(column, 0) + blank

    def add_parse_fail(self, column: str, count: int) -> None:
        """Record parse failures to surface data type issues."""
        self.parse_fail_counts[column] = self.parse_fail_counts.get(column, 0) + count

    def add_duplicate(self, value: str | None) -> None:
        """Count duplicates and keep a small set of examples."""
        self.duplicates_count += 1
        if value and len(self.duplicate_examples) < 5:
            self.duplicate_examples.append(value)

    def add_category_inconsistency(self, raw_value: str | None) -> None:
        """Track category normalization inconsistencies for review."""
        self.category_inconsistent_count += 1
        if raw_value and len(self.category_examples) < 5:
            self.category_examples.append(raw_value)

    def set_outliers(self, info: dict[str, dict[str, float]]) -> None:
        """Attach outlier summaries computed after load."""
        self.outlier_info = info

    def set_null_heavy(self, columns: dict[str, float]) -> None:
        """Capture columns with extreme null ratios for alerting."""
        self.null_heavy = columns

    def set_post_load_checks(self, checks: dict[str, dict[str, float]]) -> None:
        """Store sanity checks computed after the ClickHouse load."""
        self.post_load_checks = checks

    def summary(self) -> dict[str, Any]:
        """Build a condensed summary used by the quality report API."""
        missing_summary = {}
        for col, missing in self.missing_counts.items():
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
        """Translate summary stats into a list of human-readable findings."""
        findings: list[dict[str, Any]] = []
        for col, stats in self.summary()["missing"].items():
            ratio = stats["ratio"]
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

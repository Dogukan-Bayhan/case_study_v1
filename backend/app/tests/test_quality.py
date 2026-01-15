"""Quality accumulator tests for missing data ratios."""

import polars as pl

from app.etl.quality import QualityAccumulator


def test_missing_ratio():
    """Verify missing/blank tracking aligns with expected counts.

    Business purpose:
        Validate quality accumulator missingness calculations.
    Why it exists:
        Prevent regressions in missing/blank ratio reporting.
    Where used:
        Test suite for ETL quality logic.
    Inputs:
        None; constructs an in-memory DataFrame.
    Returns:
        None; asserts expected summary values.
    """
    df = pl.DataFrame({"a": [1, None, 3], "b": ["", "x", None]})
    quality = QualityAccumulator()
    # Accumulate missing and blank counts to compare against expectations.
    quality.add_rows(df.height)
    for col in df.columns:
        series = df[col]
        nulls = int(series.is_null().sum())
        blanks = int(series.str.strip_chars().eq("").sum()) if series.dtype == pl.Utf8 else 0
        quality.add_missing(col, nulls + blanks, blanks)

    summary = quality.summary()
    assert summary["missing"]["a"]["missing"] == 1
    assert summary["missing"]["b"]["missing"] == 2

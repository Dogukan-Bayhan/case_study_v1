"""Quality accumulator tests for missing data ratios."""

import polars as pl

from app.etl.quality import QualityAccumulator


def test_missing_ratio():
    """Verify missing/blank tracking aligns with expected counts."""
    df = pl.DataFrame({"a": [1, None, 3], "b": ["", "x", None]})
    quality = QualityAccumulator()
    quality.add_rows(df.height)
    for col in df.columns:
        series = df[col]
        nulls = int(series.is_null().sum())
        blanks = int(series.str.strip_chars().eq("").sum()) if series.dtype == pl.Utf8 else 0
        quality.add_missing(col, nulls + blanks, blanks)

    summary = quality.summary()
    assert summary["missing"]["a"]["missing"] == 1
    assert summary["missing"]["b"]["missing"] == 2

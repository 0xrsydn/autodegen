from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest

from autodegen.oracle.ingest import DataQualityError, validate_ohlcv


def _base_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "timestamp": [
                datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                datetime(2025, 1, 1, 1, 0, tzinfo=UTC),
            ],
            "open": [100.0, 105.0],
            "high": [110.0, 112.0],
            "low": [95.0, 100.0],
            "close": [105.0, 108.0],
            "volume": [10.0, 12.0],
        }
    )


def test_monotonic_timestamp_passes() -> None:
    validate_ohlcv(_base_df())


def test_monotonic_timestamp_fails() -> None:
    df = _base_df().sort("timestamp", descending=True)
    with pytest.raises(DataQualityError, match="non-monotonic timestamp"):
        validate_ohlcv(df)


def test_duplicate_detection() -> None:
    df = _base_df().with_columns(pl.lit(datetime(2025, 1, 1, 0, 0, tzinfo=UTC)).alias("timestamp"))
    with pytest.raises(DataQualityError, match="duplicate timestamp"):
        validate_ohlcv(df)


def test_ohlc_relationship_valid() -> None:
    validate_ohlcv(_base_df())


def test_ohlc_relationship_invalid() -> None:
    df = _base_df().with_columns(pl.lit(90.0).alias("high"))
    with pytest.raises(DataQualityError, match="OHLC violation"):
        validate_ohlcv(df)


def test_volume_non_negative() -> None:
    df = _base_df().with_columns(pl.lit(-1.0).alias("volume"))
    with pytest.raises(DataQualityError, match="negative volume"):
        validate_ohlcv(df)


def test_null_detection() -> None:
    df = _base_df().with_columns(pl.lit(None).alias("close"))
    with pytest.raises(DataQualityError, match="null detected"):
        validate_ohlcv(df)

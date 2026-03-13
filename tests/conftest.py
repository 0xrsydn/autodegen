from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest


@pytest.fixture
def sample_rows_ms() -> list[list[float]]:
    return [
        [1735689600000, 100.0, 110.0, 95.0, 105.0, 10.0],
        [1735693200000, 105.0, 112.0, 100.0, 108.0, 12.0],
    ]


@pytest.fixture
def sample_df() -> pl.DataFrame:
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

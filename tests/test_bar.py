from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from autodegen.oracle.backtest import Bar, load_bars


def test_bar_dataclass_creation() -> None:
    bar = Bar(
        timestamp=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=10.0,
    )
    assert bar.close == 1.5
    assert bar.funding_rate is None


def test_loader_reads_parquet_and_yields_bars(tmp_path: Path) -> None:
    target = tmp_path / "hyperliquid" / "BTC-USDT"
    target.mkdir(parents=True)
    pl.DataFrame(
        {
            "timestamp": [datetime(2025, 1, 1, 0, 0, tzinfo=UTC)],
            "open": [100.0],
            "high": [110.0],
            "low": [95.0],
            "close": [105.0],
            "volume": [10.0],
            "funding_rate": [0.001],
            "quote_volume": [1000.0],
        }
    ).write_parquet(target / "2025-01.parquet")

    bars = list(load_bars(tmp_path, "hyperliquid", "BTC/USDT"))
    assert len(bars) == 1
    assert bars[0].funding_rate == 0.001
    assert bars[0].quote_volume == 1000.0


def test_loader_missing_optional_fields(tmp_path: Path) -> None:
    target = tmp_path / "hyperliquid" / "BTC-USDT"
    target.mkdir(parents=True)
    pl.DataFrame(
        {
            "timestamp": [datetime(2025, 1, 1, 0, 0, tzinfo=UTC)],
            "open": [100.0],
            "high": [110.0],
            "low": [95.0],
            "close": [105.0],
            "volume": [10.0],
        }
    ).write_parquet(target / "2025-01.parquet")

    bars = list(load_bars(tmp_path, "hyperliquid", "BTC/USDT"))
    assert bars[0].funding_rate is None
    assert bars[0].quote_volume is None

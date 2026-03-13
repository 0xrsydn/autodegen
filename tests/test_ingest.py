from __future__ import annotations

from pathlib import Path

import polars as pl

from autodegen.oracle import ingest
from autodegen.oracle.ingest import IngestConfig, ingest_ohlcv


class FakeExchange:
    def __init__(self, *_args, **_kwargs):
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe="1h", since=None, limit=1000):
        self.calls.append((symbol, timeframe, since, limit))
        if since is None:
            return [
                [1735689600000, 100.0, 110.0, 95.0, 105.0, 10.0],
                [1735693200000, 105.0, 112.0, 100.0, 108.0, 12.0],
            ]
        return [[1735696800000, 108.0, 115.0, 107.0, 114.0, 9.0]]


def test_ingest_writes_valid_parquet(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ingest.ccxt, "hyperliquid", FakeExchange)
    cfg = IngestConfig(data_dir=tmp_path)

    paths = ingest_ohlcv(cfg)
    assert len(paths) == 1

    df = pl.read_parquet(paths[0])
    assert df.columns == ["timestamp", "open", "high", "low", "close", "volume"]
    assert df.dtypes[0].is_temporal()
    assert df.dtypes[1:] == [pl.Float64, pl.Float64, pl.Float64, pl.Float64, pl.Float64]
    assert len(df) == 2


def test_ingest_incremental_appends(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ingest.ccxt, "hyperliquid", FakeExchange)
    cfg = IngestConfig(data_dir=tmp_path)

    ingest_ohlcv(cfg)
    ingest_ohlcv(cfg)

    out = tmp_path / "hyperliquid" / "BTC-USDT" / "2025-01.parquet"
    df = pl.read_parquet(out)
    assert len(df) == 3
    assert df["timestamp"].is_sorted()


def test_parquet_schema(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ingest.ccxt, "hyperliquid", FakeExchange)
    cfg = IngestConfig(data_dir=tmp_path)

    paths = ingest_ohlcv(cfg)
    schema = pl.read_parquet_schema(paths[0])
    assert schema["timestamp"].is_temporal()
    assert schema["open"] == pl.Float64
    assert schema["high"] == pl.Float64
    assert schema["low"] == pl.Float64
    assert schema["close"] == pl.Float64
    assert schema["volume"] == pl.Float64

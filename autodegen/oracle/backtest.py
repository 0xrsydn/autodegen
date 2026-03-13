from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import polars as pl


@dataclass(slots=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    funding_rate: float | None = None
    quote_volume: float | None = None


def load_bars(data_dir: Path, exchange: str, pair: str) -> Iterator[Bar]:
    base = data_dir / exchange / pair.replace("/", "-")
    if not base.exists():
        return iter(())

    files = sorted(base.glob("*.parquet"))
    if not files:
        return iter(())

    df = pl.concat([pl.read_parquet(f) for f in files]).sort("timestamp")
    for row in df.iter_rows(named=True):
        yield Bar(
            timestamp=row["timestamp"],
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            funding_rate=float(row["funding_rate"]) if row.get("funding_rate") is not None else None,
            quote_volume=float(row["quote_volume"]) if row.get("quote_volume") is not None else None,
        )

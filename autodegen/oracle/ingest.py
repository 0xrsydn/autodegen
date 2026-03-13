from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import ccxt  # type: ignore
import polars as pl

REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


class DataQualityError(ValueError):
    """Raised when OHLCV quality checks fail."""


@dataclass(slots=True)
class IngestConfig:
    exchange: str = "hyperliquid"
    pair: str = "BTC/USDT"
    timeframe: str = "1h"
    data_dir: Path = Path("data")
    limit: int = 1000


def _target_dir(data_dir: Path, exchange: str, pair: str) -> Path:
    return data_dir / exchange / pair.replace("/", "-")


def _month_path(data_dir: Path, exchange: str, pair: str, ts: datetime) -> Path:
    return _target_dir(data_dir, exchange, pair) / f"{ts:%Y-%m}.parquet"


def validate_ohlcv(df: pl.DataFrame) -> None:
    if any(c not in df.columns for c in REQUIRED_COLUMNS):
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        raise DataQualityError(f"missing required columns: {missing}")

    null_rows = df.filter(pl.any_horizontal([pl.col(c).is_null() for c in REQUIRED_COLUMNS]))
    if len(null_rows) > 0:
        row = null_rows.row(0, named=True)
        raise DataQualityError(f"null detected at row={row}")

    if not df["timestamp"].is_sorted():
        for i in range(1, len(df)):
            if df["timestamp"][i] < df["timestamp"][i - 1]:
                raise DataQualityError(
                    f"non-monotonic timestamp at index={i}: {df['timestamp'][i - 1]} -> {df['timestamp'][i]}"
                )

    dupes = df.group_by("timestamp").len().filter(pl.col("len") > 1)
    if len(dupes) > 0:
        ts = dupes["timestamp"][0]
        raise DataQualityError(f"duplicate timestamp detected: {ts}")

    bad_high = df.filter(pl.col("high") < pl.max_horizontal("open", "close"))
    if len(bad_high) > 0:
        row = bad_high.row(0, named=True)
        raise DataQualityError(f"OHLC violation high < max(open,close) at row={row}")

    bad_low = df.filter(pl.col("low") > pl.min_horizontal("open", "close"))
    if len(bad_low) > 0:
        row = bad_low.row(0, named=True)
        raise DataQualityError(f"OHLC violation low > min(open,close) at row={row}")

    bad_vol = df.filter(pl.col("volume") < 0)
    if len(bad_vol) > 0:
        row = bad_vol.row(0, named=True)
        raise DataQualityError(f"negative volume at row={row}")


def _normalize_ohlcv(rows: Iterable[list[float]]) -> pl.DataFrame:
    df = pl.DataFrame(rows, schema=["timestamp", "open", "high", "low", "close", "volume"], orient="row")
    df = df.with_columns(
        [
            pl.from_epoch("timestamp", time_unit="ms").alias("timestamp"),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("volume").cast(pl.Float64),
        ]
    )
    return df.select(REQUIRED_COLUMNS)


def _last_timestamp(data_dir: Path, exchange: str, pair: str) -> datetime | None:
    target = _target_dir(data_dir, exchange, pair)
    if not target.exists():
        return None
    files = sorted(target.glob("*.parquet"))
    if not files:
        return None

    latest = pl.concat([pl.read_parquet(f).select("timestamp") for f in files]).sort("timestamp")
    if len(latest) == 0:
        return None
    return latest["timestamp"][-1]


def _write_partitioned(df: pl.DataFrame, data_dir: Path, exchange: str, pair: str) -> list[Path]:
    if len(df) == 0:
        return []
    out: list[Path] = []
    target = _target_dir(data_dir, exchange, pair)
    target.mkdir(parents=True, exist_ok=True)

    by_month = df.with_columns(pl.col("timestamp").dt.strftime("%Y-%m").alias("month")).partition_by("month")
    for part in by_month:
        month = part["month"][0]
        path = target / f"{month}.parquet"
        payload = part.drop("month").sort("timestamp")
        if path.exists():
            existing = pl.read_parquet(path)
            payload = pl.concat([existing, payload]).unique(subset=["timestamp"], keep="last").sort("timestamp")
        validate_ohlcv(payload)
        payload.write_parquet(path)
        out.append(path)
    return out


def ingest_ohlcv(config: IngestConfig) -> list[Path]:
    ex_class = getattr(ccxt, config.exchange)
    exchange = ex_class({"enableRateLimit": True})

    since = _last_timestamp(config.data_dir, config.exchange, config.pair)
    since_ms = int(since.timestamp() * 1000) + 1 if since else None

    rows = exchange.fetch_ohlcv(config.pair, timeframe=config.timeframe, since=since_ms, limit=config.limit)
    if not rows:
        return []

    df = _normalize_ohlcv(rows)
    validate_ohlcv(df)
    return _write_partitioned(df, config.data_dir, config.exchange, config.pair)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Ingest OHLCV data and write monthly parquet partitions")
    p.add_argument("--exchange", default="hyperliquid")
    p.add_argument("--pair", default="BTC/USDT")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--limit", type=int, default=1000)
    return p


def main() -> int:
    args = build_parser().parse_args()
    cfg = IngestConfig(
        exchange=args.exchange,
        pair=args.pair,
        timeframe=args.timeframe,
        data_dir=Path(args.data_dir),
        limit=args.limit,
    )
    paths = ingest_ohlcv(cfg)
    print(f"wrote {len(paths)} parquet file(s)")
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

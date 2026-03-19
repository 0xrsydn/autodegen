from __future__ import annotations

import argparse
import csv
import math
import os
import time
from collections import namedtuple
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import fmean

import ccxt  # type: ignore
import polars as pl

Bar = namedtuple("Bar", ["timestamp", "open", "high", "low", "close", "volume"])
REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
EPS = 1e-12
CANONICAL_EXCHANGE = "binance"
CANONICAL_PAIR = "BTC/USDT:USDT"
CANONICAL_TIMEFRAME = "1h"
CANONICAL_START = datetime(2020, 1, 1, tzinfo=UTC)
FRESHNESS_TOLERANCE = timedelta(hours=48)
TF_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}
MAX_PARAMS = 12


class DataQualityError(ValueError):
    pass


@dataclass
class BacktestResult:
    fills: list[dict]
    equity_curve: list[float]
    position_history: list[float]
    cash: float
    position: float
    days_elapsed: float


@dataclass
class DatasetSummary:
    exchange: str
    pair: str
    timeframe: str
    first_bar: datetime | None
    last_bar: datetime | None
    bar_count: int
    total_days: float
    required_bars: int
    full_wf_ready: bool


def validate_ohlcv(df: pl.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataQualityError(f"missing required columns: {missing}")

    if len(df) == 0:
        return

    null_rows = df.filter(pl.any_horizontal([pl.col(c).is_null() for c in REQUIRED_COLUMNS]))
    if len(null_rows) > 0:
        raise DataQualityError("null values found in required columns")

    if not df["timestamp"].is_sorted():
        raise DataQualityError("timestamps must be monotonic ascending")

    dupes = df.group_by("timestamp").len().filter(pl.col("len") > 1)
    if len(dupes) > 0:
        raise DataQualityError("duplicate timestamps detected")

    if len(df.filter(pl.col("high") < pl.max_horizontal("open", "close"))) > 0:
        raise DataQualityError("OHLC violation: high < max(open, close)")

    if len(df.filter(pl.col("low") > pl.min_horizontal("open", "close"))) > 0:
        raise DataQualityError("OHLC violation: low > min(open, close)")

    if len(df.filter(pl.col("volume") < 0)) > 0:
        raise DataQualityError("negative volume detected")


def _target_dir(data_dir: str, exchange: str, pair: str, timeframe: str | None = None) -> Path:
    base = Path(data_dir) / exchange / pair.replace("/", "-").replace(":", "-")
    if timeframe and timeframe != CANONICAL_TIMEFRAME:
        return base / timeframe
    return base


def _normalize_pair(exchange: str, pair: str) -> str:
    if exchange == CANONICAL_EXCHANGE and pair == "BTC/USDT":
        return CANONICAL_PAIR
    return pair


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_start(start: str | None) -> datetime:
    if start is None:
        return CANONICAL_START
    cleaned = start.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(cleaned)
    return _as_utc(parsed)


def _timeframe_seconds(timeframe: str) -> int:
    secs = TF_SECONDS.get(timeframe)
    if secs is None:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    return secs


def bars_per_year(timeframe: str) -> int:
    """Number of bars in a year for a given timeframe."""
    return int(365.25 * 24 * 3600 / _timeframe_seconds(timeframe))


def target_train_bars(timeframe: str) -> int:
    return int(180 * 24 * 3600 / _timeframe_seconds(timeframe))


def target_test_bars(timeframe: str) -> int:
    return int(45 * 24 * 3600 / _timeframe_seconds(timeframe))


def min_validation_bars(timeframe: str) -> int:
    return int(90 * 24 * 3600 / _timeframe_seconds(timeframe))


def _required_total_bars(timeframe: str, n_folds: int = 6, validation_pct: float = 0.15) -> int:
    wf_bars = target_train_bars(timeframe) + (n_folds * target_test_bars(timeframe))
    return math.ceil(wf_bars / (1.0 - validation_pct))


def _load_time_bounds(target: Path) -> tuple[datetime | None, datetime | None]:
    files = sorted(target.glob("*.parquet"))
    if not files:
        return None, None

    earliest_ts: datetime | None = None
    latest_ts: datetime | None = None
    for file in files:
        part = pl.read_parquet(file).select("timestamp").sort("timestamp")
        if len(part) == 0:
            continue
        first_ts = part["timestamp"][0]
        ts = part["timestamp"][-1]
        if earliest_ts is None or first_ts < earliest_ts:
            earliest_ts = first_ts
        if latest_ts is None or ts > latest_ts:
            latest_ts = ts

    return earliest_ts, latest_ts


def load_ohlcv(
    data_dir: str = "data",
    exchange: str = CANONICAL_EXCHANGE,
    pair: str = CANONICAL_PAIR,
    timeframe: str = CANONICAL_TIMEFRAME,
) -> pl.DataFrame:
    pair = _normalize_pair(exchange, pair)
    target = _target_dir(data_dir, exchange, pair, timeframe)
    files = sorted(target.glob("*.parquet"))
    if not files:
        return pl.DataFrame({c: [] for c in REQUIRED_COLUMNS})

    df = pl.concat([pl.read_parquet(f) for f in files]).sort("timestamp").unique(subset=["timestamp"], keep="last")
    validate_ohlcv(df)
    return df.select(REQUIRED_COLUMNS)


def summarize_dataset(
    data_dir: str = "data",
    exchange: str = CANONICAL_EXCHANGE,
    pair: str = CANONICAL_PAIR,
    timeframe: str = CANONICAL_TIMEFRAME,
    n_folds: int = 6,
    validation_pct: float = 0.15,
) -> DatasetSummary:
    pair = _normalize_pair(exchange, pair)
    df = load_ohlcv(data_dir=data_dir, exchange=exchange, pair=pair, timeframe=timeframe)
    timestamps = df["timestamp"].to_list() if len(df) > 0 else []

    expected_delta = timedelta(seconds=_timeframe_seconds(timeframe))
    for prev, curr in zip(timestamps, timestamps[1:]):
        delta = curr - prev
        if delta != expected_delta:
            raise DataQualityError(
                f"timestamp gap detected: {_as_utc(prev).isoformat()} -> {_as_utc(curr).isoformat()} ({delta})"
            )

    first_bar = _as_utc(timestamps[0]) if timestamps else None
    last_bar = _as_utc(timestamps[-1]) if timestamps else None
    bar_count = len(df)
    required_bars = _required_total_bars(timeframe=timeframe, n_folds=n_folds, validation_pct=validation_pct)
    bars_per_day = 86400 / _timeframe_seconds(timeframe)

    return DatasetSummary(
        exchange=exchange,
        pair=pair,
        timeframe=timeframe,
        first_bar=first_bar,
        last_bar=last_bar,
        bar_count=bar_count,
        total_days=bar_count / bars_per_day,
        required_bars=required_bars,
        full_wf_ready=bar_count >= required_bars,
    )


def print_dataset_summary(summary: DatasetSummary) -> None:
    first_bar = summary.first_bar.isoformat() if summary.first_bar else "NONE"
    last_bar = summary.last_bar.isoformat() if summary.last_bar else "NONE"
    print(f"dataset_exchange={summary.exchange}")
    print(f"dataset_pair={summary.pair}")
    print(f"dataset_timeframe={summary.timeframe}")
    print(f"dataset_first_bar={first_bar}")
    print(f"dataset_last_bar={last_bar}")
    print(f"dataset_bars={summary.bar_count}")
    print(f"dataset_days={summary.total_days:.1f}")
    print(f"dataset_required_bars={summary.required_bars}")
    print(f"dataset_full_wf_ready={'PASS' if summary.full_wf_ready else 'FAIL'}")


def validate_dataset(
    data_dir: str = "data",
    exchange: str = CANONICAL_EXCHANGE,
    pair: str = CANONICAL_PAIR,
    timeframe: str = CANONICAL_TIMEFRAME,
    start: datetime = CANONICAL_START,
    freshness_tolerance: timedelta = FRESHNESS_TOLERANCE,
    n_folds: int = 6,
    validation_pct: float = 0.15,
) -> DatasetSummary:
    summary = summarize_dataset(
        data_dir=data_dir,
        exchange=exchange,
        pair=pair,
        timeframe=timeframe,
        n_folds=n_folds,
        validation_pct=validation_pct,
    )

    if summary.bar_count == 0:
        raise DataQualityError(f"no local data found for {summary.exchange} {summary.pair} in {data_dir}")
    if summary.first_bar is None or summary.first_bar > start:
        actual = summary.first_bar.isoformat() if summary.first_bar else "NONE"
        raise DataQualityError(f"dataset starts too late: expected <= {start.isoformat()}, got {actual}")
    if not summary.full_wf_ready:
        raise DataQualityError(
            f"insufficient history for canonical walk-forward: need >= {summary.required_bars} bars, got {summary.bar_count}"
        )
    if summary.last_bar is None:
        raise DataQualityError("dataset is missing a last timestamp")
    if datetime.now(UTC) - summary.last_bar > freshness_tolerance:
        raise DataQualityError(
            f"dataset is stale: last bar {summary.last_bar.isoformat()} is older than {freshness_tolerance}"
        )

    return summary


def fetch_data(
    exchange: str = CANONICAL_EXCHANGE,
    pair: str = CANONICAL_PAIR,
    timeframe: str = CANONICAL_TIMEFRAME,
    data_dir: str = "data",
    start: str | None = None,
) -> list[Path]:
    """Fetch OHLCV data via ccxt with since-based pagination; writes monthly parquet files."""
    pair = _normalize_pair(exchange, pair)
    start_dt = _parse_start(start)

    _ccxt_opts: dict = {"enableRateLimit": True}
    # Support SOCKS proxy via env var (e.g. for regions where Binance is blocked)
    _proxy = os.environ.get("CCXT_PROXY")
    if _proxy:
        _ccxt_opts["proxies"] = {"http": _proxy, "https": _proxy}
    ex = getattr(ccxt, exchange)(_ccxt_opts)
    target = _target_dir(data_dir, exchange, pair, timeframe)
    target.mkdir(parents=True, exist_ok=True)

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    earliest_ts, latest_ts = _load_time_bounds(target)
    if latest_ts is None:
        since_ms = int(start_dt.timestamp() * 1000)
    elif earliest_ts is None or _as_utc(earliest_ts) > start_dt:
        since_ms = int(start_dt.timestamp() * 1000)
    else:
        since_ms = int(_as_utc(latest_ts).timestamp() * 1000) + 1

    all_rows: list[list[float | int]] = []
    limit = 1000
    step_ms = ccxt.Exchange.parse_timeframe(timeframe) * 1000

    while True:
        rows = ex.fetch_ohlcv(pair, timeframe=timeframe, since=since_ms, limit=limit)
        if not rows:
            break

        all_rows.extend(rows)
        last_ts = int(rows[-1][0])
        next_since = last_ts + step_ms

        if next_since <= since_ms:
            break

        since_ms = next_since

        if len(rows) < limit and since_ms >= now_ms - step_ms:
            break

        time.sleep(max(getattr(ex, "rateLimit", 200), 200) / 1000.0)

    if not all_rows:
        return []

    df = (
        pl.DataFrame(all_rows, schema=REQUIRED_COLUMNS, orient="row")
        .with_columns(
            pl.from_epoch("timestamp", time_unit="ms").alias("timestamp"),
            *[pl.col(c).cast(pl.Float64) for c in ["open", "high", "low", "close", "volume"]],
        )
        .select(REQUIRED_COLUMNS)
        .sort("timestamp")
        .unique(subset=["timestamp"], keep="last")
    )
    validate_ohlcv(df)

    out_paths: list[Path] = []
    monthly = df.with_columns(pl.col("timestamp").dt.strftime("%Y-%m").alias("month")).partition_by("month")
    for part in monthly:
        month = part["month"][0]
        out = target / f"{month}.parquet"
        payload = part.drop("month").sort("timestamp")

        if out.exists():
            payload = pl.concat([pl.read_parquet(out), payload]).unique(subset=["timestamp"], keep="last").sort("timestamp")

        validate_ohlcv(payload)
        payload.write_parquet(out)
        out_paths.append(out)

    return sorted(out_paths)


def load_bars(
    data_dir: str = "data",
    exchange: str = CANONICAL_EXCHANGE,
    pair: str = CANONICAL_PAIR,
    timeframe: str = CANONICAL_TIMEFRAME,
) -> list[Bar]:
    df = load_ohlcv(data_dir=data_dir, exchange=exchange, pair=pair, timeframe=timeframe)
    return [Bar(*row) for row in df.select(REQUIRED_COLUMNS).iter_rows()]


def synthetic_bars(n: int = 500) -> list[Bar]:
    bars: list[Bar] = []
    ts = datetime(2024, 1, 1, tzinfo=UTC)
    price = 100.0
    for i in range(n):
        drift = math.sin(i / 20) * 0.5 + 0.1
        o = price
        c = max(1.0, o + drift)
        h = max(o, c) + 0.5
        l = min(o, c) - 0.5
        v = 1000.0 + (i % 25) * 10
        bars.append(Bar(ts, o, h, l, c, v))
        ts = ts + timedelta(hours=1)
        price = c
    return bars


def _fill_price(side: str, size: float, bar: Bar, slippage_factor: float) -> float:
    participation = 0.0 if bar.volume <= 0 else size / bar.volume
    slip_pct = slippage_factor * participation
    raw = bar.open * (1 + slip_pct) if side == "buy" else bar.open * (1 - slip_pct)
    return max(bar.low, min(bar.high, raw))


def run_backtest(
    strategy,
    bars: list[Bar],
    initial_cash: float = 10000,
    maker_fee: float = 0.0002,
    taker_fee: float = 0.0005,
    slippage_factor: float = 0.1,
) -> BacktestResult:
    if not bars:
        return BacktestResult([], [], [], initial_cash, 0.0, 0.0)

    strategy.initialize([])
    cash = float(initial_cash)
    position = 0.0
    avg_entry = 0.0
    equity_curve: list[float] = []
    position_history: list[float] = []
    fills: list[dict] = []
    pending: list[dict] = []

    for bar in bars:
        for signal in pending:
            side = signal["side"]
            size = float(signal["size"])
            price = _fill_price(side, size, bar, slippage_factor)
            qty = size if side == "buy" else -size
            fee = abs(size * price) * taker_fee

            cash += -(qty * price) - fee
            prev_position = position
            prev_abs = abs(prev_position)
            prev_sign = 1.0 if prev_position > 0 else (-1.0 if prev_position < 0 else 0.0)
            close_qty = min(prev_abs, abs(qty)) if prev_sign != 0 and prev_sign != (1.0 if qty > 0 else -1.0) else 0.0
            pnl = 0.0

            if close_qty > 0:
                if prev_sign > 0:
                    pnl = (price - avg_entry) * close_qty
                else:
                    pnl = (avg_entry - price) * close_qty

            position = prev_position + qty

            if abs(position) < EPS:
                position = 0.0
                avg_entry = 0.0
            elif prev_position == 0 or (prev_sign == (1.0 if qty > 0 else -1.0)):
                new_abs = abs(position)
                avg_entry = ((avg_entry * prev_abs) + (price * abs(qty))) / max(new_abs, EPS)
            elif (prev_position > 0 > position) or (prev_position < 0 < position):
                avg_entry = price

            fills.append(
                {
                    "timestamp": bar.timestamp,
                    "side": side,
                    "size": size,
                    "price": price,
                    "fee": fee,
                    "pnl": pnl,
                    "entry_value": close_qty * avg_entry if close_qty > 0 else 0.0,
                    "is_close": close_qty > 0,
                }
            )

        pending = list(strategy.on_bar(bar, {"cash": cash, "position": position, "equity": cash + position * bar.close}) or [])
        equity_curve.append(cash + position * bar.close)
        position_history.append(position)

    days_elapsed = max(1e-9, (bars[-1].timestamp - bars[0].timestamp).total_seconds() / 86400) if len(bars) > 1 else 1e-9
    return BacktestResult(
        fills=fills,
        equity_curve=equity_curve,
        position_history=position_history,
        cash=cash,
        position=position,
        days_elapsed=days_elapsed,
    )


def _bar_returns(result: BacktestResult) -> list[float]:
    if len(result.equity_curve) < 2:
        return []
    rets: list[float] = []
    for prev, curr in zip(result.equity_curve[:-1], result.equity_curve[1:]):
        if abs(prev) < EPS:
            continue
        rets.append((curr / prev) - 1.0)
    return rets


def bar_return_sharpe(result: BacktestResult, timeframe: str = CANONICAL_TIMEFRAME) -> float:
    rets = _bar_returns(result)
    if len(rets) < 2:
        return 0.0
    mean_r = fmean(rets)
    var = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(max(var, 0.0))
    if std < EPS:
        return 0.0
    return (mean_r / std) * math.sqrt(bars_per_year(timeframe))


def sortino(result: BacktestResult, timeframe: str = CANONICAL_TIMEFRAME) -> float:
    rets = _bar_returns(result)
    if len(rets) < 2:
        return 0.0
    mean_r = fmean(rets)
    downside = [r for r in rets if r < 0]
    if not downside:
        return 10.0 if mean_r > 0 else 0.0
    dd = math.sqrt(sum(r * r for r in downside) / len(downside))
    if dd < EPS:
        return 0.0
    return (mean_r / dd) * math.sqrt(bars_per_year(timeframe))


def max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    mdd = 0.0
    for e in equity_curve:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak)
    return mdd


def cagr(result: BacktestResult) -> float:
    if len(result.equity_curve) < 2:
        return 0.0
    start = result.equity_curve[0]
    end = result.equity_curve[-1]
    if start <= 0 or end <= 0:
        return 0.0
    years = max(result.days_elapsed / 365.0, 1e-9)
    return (end / start) ** (1.0 / years) - 1.0


def calmar(result: BacktestResult) -> float:
    return cagr(result) / max(max_drawdown(result.equity_curve), EPS)


def trade_return_sharpe(result: BacktestResult) -> float:
    closes = [f for f in result.fills if f.get("is_close") and f.get("entry_value", 0) > 0]
    if len(closes) < 2:
        return 0.0
    returns = [f["pnl"] / f["entry_value"] for f in closes]
    mean_r = fmean(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    if std < EPS:
        return 0.0
    years = max(result.days_elapsed / 365.0, 1e-9)
    trades_per_year = len(returns) / years
    return (mean_r / std) * math.sqrt(trades_per_year)


def profit_factor(result: BacktestResult) -> float:
    closes = [f for f in result.fills if f.get("is_close")]
    wins = sum(max(f["pnl"], 0.0) for f in closes)
    losses = sum(min(f["pnl"], 0.0) for f in closes)
    if abs(losses) < EPS:
        return 10.0 if wins > 0 else 0.0
    return wins / abs(losses)


def win_rate(result: BacktestResult) -> float:
    closes = [f for f in result.fills if f.get("is_close")]
    if not closes:
        return 0.0
    return sum(1 for f in closes if f["pnl"] > 0) / len(closes)


def exposure(result: BacktestResult) -> float:
    if not result.position_history:
        return 0.0
    active = sum(1 for p in result.position_history if abs(p) > EPS)
    return active / len(result.position_history)


def closed_trades(result: BacktestResult) -> int:
    return len([f for f in result.fills if f.get("is_close")])


def summarize_result(result: BacktestResult, timeframe: str = CANONICAL_TIMEFRAME) -> dict[str, float]:
    return {
        "bar_sharpe": bar_return_sharpe(result, timeframe=timeframe),
        "trade_sharpe": trade_return_sharpe(result),
        "sortino": sortino(result, timeframe=timeframe),
        "calmar": calmar(result),
        "profit_factor": profit_factor(result),
        "win_rate": win_rate(result),
        "exposure": exposure(result),
        "maxdd": max_drawdown(result.equity_curve),
        "trades": float(closed_trades(result)),
    }


def walk_forward_splits(
    bars: list[Bar],
    n_folds: int = 6,
    timeframe: str = CANONICAL_TIMEFRAME,
) -> list[tuple[list[Bar], list[Bar]]]:
    if len(bars) < n_folds + 2:
        return []

    target_train = target_train_bars(timeframe)
    target_test = target_test_bars(timeframe)
    target_total = target_train + (n_folds * target_test)
    bars_per_day = 86400 / _timeframe_seconds(timeframe)

    if len(bars) < target_total:
        raise DataQualityError(
            f"insufficient walk-forward history: need {target_total} bars ({target_total / bars_per_day:.1f}d), got {len(bars)} bars ({len(bars) / bars_per_day:.1f}d)"
        )

    train_size = target_train
    test_size = target_test
    available = len(bars) - train_size
    step = max(test_size, (available - test_size) // max(n_folds - 1, 1))

    splits: list[tuple[list[Bar], list[Bar]]] = []
    for i in range(n_folds):
        test_start = train_size + i * step
        test_end = test_start + test_size
        if test_end > len(bars):
            break
        splits.append((bars[:test_start], bars[test_start:test_end]))

    return splits


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def composite_score(
    wf_bar_sharpe: float,
    val_bar_sharpe: float,
    wf_sortino: float,
    wf_calmar: float,
    wf_profit_factor: float,
    negative_fold_ratio: float,
    holdout_decay: float,
    fold_regime_gap: float,
) -> float:
    return (
        0.30 * _clip(wf_bar_sharpe / 3.0, 0.0, 1.0)
        + 0.10 * _clip(val_bar_sharpe / 2.0, 0.0, 1.0)
        + 0.10 * _clip(wf_sortino / 5.0, 0.0, 1.0)
        + 0.10 * _clip(wf_calmar / 3.0, 0.0, 1.0)
        + 0.10 * _clip((wf_profit_factor - 1.0) / 2.0, 0.0, 1.0)
        + 0.10 * (1.0 - negative_fold_ratio)
        + 0.10 * min(holdout_decay, 1.0)
        + 0.10 * _clip(1.0 - fold_regime_gap, 0.0, 1.0)
    )


def _append_result_row(path: Path, row: dict[str, str]) -> None:
    header = [
        "timestamp",
        "name",
        "composite",
        "bar_sharpe_wf",
        "bar_sharpe_val",
        "decay",
        "maxdd_wf",
        "maxdd_val",
        "trades_wf",
        "trades_val",
        "fold_std",
        "neg_folds",
        "profit_factor",
        "calmar",
        "n_params",
        "status",
    ]
    exists = path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        if not exists:
            w.writerow(header)
        w.writerow([row[k] for k in header])


def evaluate(
    strategy_cls,
    bars: list[Bar],
    n_folds: int = 6,
    validation_pct: float = 0.15,
    timeframe: str = CANONICAL_TIMEFRAME,
):
    if len(bars) < 100:
        raise DataQualityError("not enough real bars for evaluation")

    cut = int(len(bars) * (1 - validation_pct))
    wf_bars = bars[:cut]
    val_bars = bars[cut:]

    min_val_bars = min_validation_bars(timeframe)
    bars_per_day = 86400 / _timeframe_seconds(timeframe)
    if len(val_bars) < min_val_bars:
        raise DataQualityError(f"validation segment shorter than 90 days ({len(val_bars) / bars_per_day:.1f}d)")

    folds = walk_forward_splits(wf_bars, n_folds=n_folds, timeframe=timeframe)

    train_metrics: list[dict[str, float]] = []
    test_metrics: list[dict[str, float]] = []
    for train, test in folds:
        train_bt = run_backtest(strategy_cls(), train)
        test_bt = run_backtest(strategy_cls(), test)
        train_metrics.append(summarize_result(train_bt, timeframe=timeframe))
        test_metrics.append(summarize_result(test_bt, timeframe=timeframe))

    val_bt = run_backtest(strategy_cls(), val_bars) if val_bars else BacktestResult([], [], [], 10000.0, 0.0, 0.0)
    val = summarize_result(val_bt, timeframe=timeframe)

    wf_bar_sharpe = fmean(m["bar_sharpe"] for m in test_metrics) if test_metrics else 0.0
    wf_sortino = fmean(m["sortino"] for m in test_metrics) if test_metrics else 0.0
    wf_calmar = fmean(m["calmar"] for m in test_metrics) if test_metrics else 0.0
    wf_profit_factor = fmean(m["profit_factor"] for m in test_metrics) if test_metrics else 0.0
    wf_maxdd = fmean(m["maxdd"] for m in test_metrics) if test_metrics else 1.0
    worst_fold_maxdd = max((m["maxdd"] for m in test_metrics), default=1.0)
    trades_per_fold = [m["trades"] for m in test_metrics]
    total_wf_trades = int(sum(trades_per_fold))
    avg_trades_per_fold = fmean(trades_per_fold) if trades_per_fold else 0.0

    test_sharpes = [m["bar_sharpe"] for m in test_metrics]
    train_sharpes = [m["bar_sharpe"] for m in train_metrics]
    holdout_decay = val["bar_sharpe"] / max(wf_bar_sharpe, EPS)
    fold_regime_gap = fmean((tr - te) for tr, te in zip(train_sharpes, test_sharpes)) if test_sharpes else 0.0
    fold_std = (pl.Series(test_sharpes).std(ddof=1) if len(test_sharpes) > 1 else 0.0) or 0.0
    negative_fold_ratio = (sum(1 for s in test_sharpes if s < 0) / len(test_sharpes)) if test_sharpes else 1.0
    n_params = len(getattr(strategy_cls, "parameters", {}))

    gates_pass = all(
        [
            total_wf_trades >= 30,
            avg_trades_per_fold >= 5,
            wf_bar_sharpe >= 0.75,
            val["bar_sharpe"] >= 0.25,
            val["trades"] >= 5,
            wf_maxdd <= 0.25,
            val["maxdd"] <= 0.30,
            worst_fold_maxdd <= 0.35,
            wf_profit_factor >= 1.10,
            holdout_decay >= 0.50,
            fold_regime_gap <= 0.75,
            fold_std <= 1.25,
            negative_fold_ratio <= 0.30,
            n_params <= MAX_PARAMS,
        ]
    )

    composite = composite_score(
        wf_bar_sharpe=wf_bar_sharpe,
        val_bar_sharpe=val["bar_sharpe"],
        wf_sortino=wf_sortino,
        wf_calmar=wf_calmar,
        wf_profit_factor=wf_profit_factor,
        negative_fold_ratio=negative_fold_ratio,
        holdout_decay=holdout_decay,
        fold_regime_gap=fold_regime_gap,
    )

    metrics = {
        "composite": composite,
        "avg_sharpe": wf_bar_sharpe,
        "avg_maxdd": wf_maxdd,
        "avg_trades": avg_trades_per_fold,
        "bar_sharpe_wf": wf_bar_sharpe,
        "bar_sharpe_val": val["bar_sharpe"],
        "decay": holdout_decay,
        "fold_regime_gap": fold_regime_gap,
        "fold_std": fold_std,
        "negative_fold_ratio": negative_fold_ratio,
        "maxdd_wf": wf_maxdd,
        "maxdd_val": val["maxdd"],
        "worst_fold_maxdd": worst_fold_maxdd,
        "profit_factor_wf": wf_profit_factor,
        "calmar_wf": wf_calmar,
        "sortino_wf": wf_sortino,
        "trades_wf": float(total_wf_trades),
        "trades_val": val["trades"],
        "win_rate_wf": fmean(m["win_rate"] for m in test_metrics) if test_metrics else 0.0,
        "exposure_wf": fmean(m["exposure"] for m in test_metrics) if test_metrics else 0.0,
        "trade_sharpe_wf": fmean(m["trade_sharpe"] for m in test_metrics) if test_metrics else 0.0,
        "hard_gates": gates_pass,
    }

    for key in [
        "composite",
        "bar_sharpe_wf",
        "bar_sharpe_val",
        "decay",
        "fold_regime_gap",
        "fold_std",
        "negative_fold_ratio",
        "maxdd_wf",
        "maxdd_val",
        "worst_fold_maxdd",
        "profit_factor_wf",
        "calmar_wf",
        "sortino_wf",
        "trades_wf",
        "trades_val",
        "win_rate_wf",
        "exposure_wf",
        "trade_sharpe_wf",
    ]:
        print(f"{key}={metrics[key]:.6f}")
    print(f"hard_gates={'PASS' if gates_pass else 'FAIL'}")

    _append_result_row(
        Path("results.tsv"),
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "name": getattr(strategy_cls, "name", "strategy"),
            "composite": f"{composite:.6f}",
            "bar_sharpe_wf": f"{wf_bar_sharpe:.6f}",
            "bar_sharpe_val": f"{val['bar_sharpe']:.6f}",
            "decay": f"{holdout_decay:.6f}",
            "maxdd_wf": f"{wf_maxdd:.6f}",
            "maxdd_val": f"{val['maxdd']:.6f}",
            "trades_wf": str(total_wf_trades),
            "trades_val": str(int(val["trades"])),
            "fold_std": f"{fold_std:.6f}",
            "neg_folds": f"{negative_fold_ratio:.6f}",
            "profit_factor": f"{wf_profit_factor:.6f}",
            "calmar": f"{wf_calmar:.6f}",
            "n_params": str(n_params),
            "status": "PASS" if gates_pass else "FAIL",
        },
    )

    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")

    fetch_p = sub.add_parser("fetch")
    fetch_p.add_argument("--exchange", default=CANONICAL_EXCHANGE)
    fetch_p.add_argument("--pair", default=CANONICAL_PAIR)
    fetch_p.add_argument("--timeframe", default=CANONICAL_TIMEFRAME)
    fetch_p.add_argument("--start", default=CANONICAL_START.isoformat())

    validate_p = sub.add_parser("validate")
    validate_p.add_argument("--exchange", default=CANONICAL_EXCHANGE)
    validate_p.add_argument("--pair", default=CANONICAL_PAIR)
    validate_p.add_argument("--timeframe", default=CANONICAL_TIMEFRAME)

    eval_p = sub.add_parser("eval")
    eval_p.add_argument("--exchange", default=CANONICAL_EXCHANGE)
    eval_p.add_argument("--pair", default=CANONICAL_PAIR)
    eval_p.add_argument("--timeframe", default=CANONICAL_TIMEFRAME)

    args = parser.parse_args()
    try:
        if args.cmd == "fetch":
            paths = fetch_data(args.exchange, args.pair, args.timeframe, start=args.start)
            print(f"wrote {len(paths)} parquet file(s)")
            for p in paths:
                print(p)
        elif args.cmd == "validate":
            summary = validate_dataset(exchange=args.exchange, pair=args.pair, timeframe=args.timeframe)
            print_dataset_summary(summary)
            print("dataset_validation=PASS")
        elif args.cmd == "eval":
            summary = validate_dataset(exchange=args.exchange, pair=args.pair, timeframe=args.timeframe)
            print_dataset_summary(summary)
            from strategy import Strategy

            bars = load_bars(exchange=args.exchange, pair=args.pair, timeframe=args.timeframe)
            evaluate(Strategy, bars, timeframe=args.timeframe)
        else:
            parser.print_help()
    except DataQualityError as exc:
        print(f"dataset_validation=FAIL")
        print(f"error={exc}")
        raise SystemExit(1) from exc

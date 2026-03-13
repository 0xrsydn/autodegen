from __future__ import annotations

import argparse
import csv
import math
import time
import warnings
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
HOURS_PER_YEAR = 8760


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


def _target_dir(data_dir: str, exchange: str, pair: str) -> Path:
    return Path(data_dir) / exchange / pair.replace("/", "-").replace(":", "-")


def _load_latest_timestamp(target: Path) -> int | None:
    files = sorted(target.glob("*.parquet"))
    if not files:
        return None

    latest_ts: datetime | None = None
    for file in files:
        part = pl.read_parquet(file).select("timestamp").sort("timestamp")
        if len(part) == 0:
            continue
        ts = part["timestamp"][-1]
        if latest_ts is None or ts > latest_ts:
            latest_ts = ts

    if latest_ts is None:
        return None
    return int(latest_ts.timestamp() * 1000) + 1


def fetch_data(
    exchange: str = "binance",
    pair: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    data_dir: str = "data",
) -> list[Path]:
    """Fetch OHLCV data via ccxt with since-based pagination; writes monthly parquet files."""
    if exchange == "binance" and pair == "BTC/USDT":
        pair = "BTC/USDT:USDT"

    ex = getattr(ccxt, exchange)({"enableRateLimit": True})
    target = _target_dir(data_dir, exchange, pair)
    target.mkdir(parents=True, exist_ok=True)

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    three_years_ago_ms = int((datetime.now(UTC) - timedelta(days=365 * 3 + 2)).timestamp() * 1000)
    since_ms = _load_latest_timestamp(target) or three_years_ago_ms

    all_rows: list[list[float | int]] = []
    limit = 1000
    step_ms = 3600 * 1000

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


def load_bars(data_dir: str = "data", exchange: str = "binance", pair: str = "BTC/USDT:USDT") -> list[Bar]:
    target = _target_dir(data_dir, exchange, pair)
    files = sorted(target.glob("*.parquet"))
    if not files:
        return []

    df = pl.concat([pl.read_parquet(f) for f in files]).sort("timestamp").unique(subset=["timestamp"], keep="last")
    validate_ohlcv(df)
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


def bar_return_sharpe(result: BacktestResult) -> float:
    rets = _bar_returns(result)
    if len(rets) < 2:
        return 0.0
    mean_r = fmean(rets)
    var = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(max(var, 0.0))
    if std < EPS:
        return 0.0
    return (mean_r / std) * math.sqrt(HOURS_PER_YEAR)


def sortino(result: BacktestResult) -> float:
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
    return (mean_r / dd) * math.sqrt(HOURS_PER_YEAR)


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


def summarize_result(result: BacktestResult) -> dict[str, float]:
    return {
        "bar_sharpe": bar_return_sharpe(result),
        "trade_sharpe": trade_return_sharpe(result),
        "sortino": sortino(result),
        "calmar": calmar(result),
        "profit_factor": profit_factor(result),
        "win_rate": win_rate(result),
        "exposure": exposure(result),
        "maxdd": max_drawdown(result.equity_curve),
        "trades": float(closed_trades(result)),
    }


def walk_forward_splits(bars: list[Bar], n_folds: int = 6) -> list[tuple[list[Bar], list[Bar]]]:
    if len(bars) < n_folds + 2:
        return []

    target_train = 180 * 24
    target_test = 45 * 24
    target_total = target_train + (n_folds * target_test)

    if len(bars) >= target_total:
        train_size = target_train
        test_size = target_test
    else:
        scale = len(bars) / target_total
        train_size = max(24, int(target_train * scale))
        test_size = max(12, int(target_test * scale))
        warnings.warn(
            f"Insufficient history for ideal 180d/45d WF. Scaled to train={train_size/24:.1f}d, test={test_size/24:.1f}d",
            RuntimeWarning,
        )

    splits: list[tuple[list[Bar], list[Bar]]] = []
    for i in range(n_folds):
        train_end = train_size + i * test_size
        test_end = train_end + test_size
        if test_end > len(bars):
            break
        splits.append((bars[:train_end], bars[train_end:test_end]))

    return splits


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def composite_score(
    wf_bar_sharpe: float,
    wf_sortino: float,
    wf_calmar: float,
    wf_profit_factor: float,
    negative_fold_ratio: float,
    holdout_decay: float,
) -> float:
    return (
        0.40 * _clip(wf_bar_sharpe / 3.0, 0.0, 1.0)
        + 0.15 * _clip(wf_sortino / 5.0, 0.0, 1.0)
        + 0.15 * _clip(wf_calmar / 3.0, 0.0, 1.0)
        + 0.10 * _clip((wf_profit_factor - 1.0) / 2.0, 0.0, 1.0)
        + 0.10 * (1.0 - negative_fold_ratio)
        + 0.10 * min(holdout_decay, 1.0)
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


def evaluate(strategy_cls, bars: list[Bar], n_folds: int = 6, validation_pct: float = 0.15):
    if len(bars) < 100:
        bars = synthetic_bars(500)

    cut = int(len(bars) * (1 - validation_pct))
    wf_bars = bars[:cut]
    val_bars = bars[cut:]

    if len(val_bars) < 90 * 24:
        warnings.warn(f"Validation segment shorter than 90 days ({len(val_bars)/24:.1f}d)", RuntimeWarning)

    folds = walk_forward_splits(wf_bars, n_folds=n_folds)

    train_metrics: list[dict[str, float]] = []
    test_metrics: list[dict[str, float]] = []
    for train, test in folds:
        train_bt = run_backtest(strategy_cls(), train)
        test_bt = run_backtest(strategy_cls(), test)
        train_metrics.append(summarize_result(train_bt))
        test_metrics.append(summarize_result(test_bt))

    val_bt = run_backtest(strategy_cls(), val_bars) if val_bars else BacktestResult([], [], [], 10000.0, 0.0, 0.0)
    val = summarize_result(val_bt)

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
    train_test_gap = fmean((tr - te) for tr, te in zip(train_sharpes, test_sharpes)) if test_sharpes else 0.0
    fold_std = (pl.Series(test_sharpes).std(ddof=1) if len(test_sharpes) > 1 else 0.0) or 0.0
    negative_fold_ratio = (sum(1 for s in test_sharpes if s < 0) / len(test_sharpes)) if test_sharpes else 1.0

    gates_pass = all(
        [
            total_wf_trades >= 30,
            avg_trades_per_fold >= 5,
            wf_bar_sharpe >= 0.75,
            val["bar_sharpe"] >= 0.25,
            wf_maxdd <= 0.25,
            val["maxdd"] <= 0.30,
            worst_fold_maxdd <= 0.35,
            wf_profit_factor >= 1.10,
            holdout_decay >= 0.50,
            train_test_gap <= 0.75,
            fold_std <= 1.25,
            negative_fold_ratio <= 0.30,
        ]
    )

    composite = composite_score(
        wf_bar_sharpe=wf_bar_sharpe,
        wf_sortino=wf_sortino,
        wf_calmar=wf_calmar,
        wf_profit_factor=wf_profit_factor,
        negative_fold_ratio=negative_fold_ratio,
        holdout_decay=holdout_decay,
    )

    metrics = {
        "composite": composite,
        "avg_sharpe": wf_bar_sharpe,
        "avg_maxdd": wf_maxdd,
        "avg_trades": avg_trades_per_fold,
        "bar_sharpe_wf": wf_bar_sharpe,
        "bar_sharpe_val": val["bar_sharpe"],
        "decay": holdout_decay,
        "train_test_gap": train_test_gap,
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
        "train_test_gap",
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
            "n_params": str(len(getattr(strategy_cls, "parameters", {}))),
            "status": "PASS" if gates_pass else "FAIL",
        },
    )

    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")

    fetch_p = sub.add_parser("fetch")
    fetch_p.add_argument("--exchange", default="binance")
    fetch_p.add_argument("--pair", default="BTC/USDT:USDT")
    fetch_p.add_argument("--timeframe", default="1h")

    sub.add_parser("eval")

    args = parser.parse_args()
    if args.cmd == "fetch":
        paths = fetch_data(args.exchange, args.pair, args.timeframe)
        print(f"wrote {len(paths)} parquet file(s)")
        for p in paths:
            print(p)
    elif args.cmd == "eval":
        from strategy import Strategy

        bars = load_bars()
        evaluate(Strategy, bars)
    else:
        parser.print_help()

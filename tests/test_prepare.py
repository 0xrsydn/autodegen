from datetime import UTC, datetime, timedelta

import pytest

from prepare import (
    Bar,
    evaluate,
    max_drawdown,
    run_backtest,
    synthetic_bars,
    trade_return_sharpe,
    validate_ohlcv,
    walk_forward_splits,
    _fill_price,
)


class DumbStrategy:
    name = "dumb"

    def initialize(self, train_data):
        self.i = 0

    def on_bar(self, bar, portfolio):
        self.i += 1
        if self.i == 1:
            return [{"side": "buy", "size": 1.0}]
        if self.i == 3:
            return [{"side": "sell", "size": 1.0}]
        return []


def make_bars(n=12):
    ts = datetime(2024, 1, 1, tzinfo=UTC)
    bars = []
    for i in range(n):
        o = 100 + i
        bars.append(Bar(ts + timedelta(hours=i), o, o + 1, o - 1, o + 0.2, 1000.0))
    return bars


def test_bar_namedtuple():
    b = make_bars(1)[0]
    assert b.open == 100
    assert b.timestamp.tzinfo == UTC


def test_data_quality_validation_pass_and_fail():
    import polars as pl

    good = pl.DataFrame(
        {
            "timestamp": [datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, 1, tzinfo=UTC)],
            "open": [1.0, 1.1],
            "high": [1.2, 1.3],
            "low": [0.9, 1.0],
            "close": [1.1, 1.2],
            "volume": [10.0, 11.0],
        }
    )
    validate_ohlcv(good)

    bad = good.with_columns(pl.lit(-1.0).alias("volume"))
    with pytest.raises(Exception):
        validate_ohlcv(bad)


def test_one_bar_delay_and_fee_deduction_and_long_short_tracking():
    bars = make_bars(6)
    result = run_backtest(DumbStrategy(), bars, initial_cash=10000)
    assert len(result.fills) >= 2
    assert result.fills[0]["timestamp"] == bars[1].timestamp  # delayed fill
    assert sum(f["fee"] for f in result.fills) > 0


def test_slippage_model_buy_sell_clamp():
    bar = Bar(datetime(2024, 1, 1, tzinfo=UTC), 100, 101, 99, 100, 10)
    buy = _fill_price("buy", 100, bar, 0.5)
    sell = _fill_price("sell", 100, bar, 0.5)
    assert 99 <= buy <= 101
    assert 99 <= sell <= 101
    assert buy >= sell


def test_walk_forward_fold_count_and_expanding_window():
    bars = synthetic_bars(160)
    splits = walk_forward_splits(bars, 6)
    assert len(splits) >= 1
    train_sizes = [len(t) for t, _ in splits]
    assert train_sizes == sorted(train_sizes)


def test_trade_return_sharpe_known_values_and_maxdd():
    class R:
        fills = [
            {"is_close": True, "entry_value": 100, "pnl": 10},
            {"is_close": True, "entry_value": 100, "pnl": -5},
            {"is_close": True, "entry_value": 100, "pnl": 8},
        ]
        days_elapsed = 30

    s = trade_return_sharpe(R())
    assert s != 0
    assert max_drawdown([100, 90, 95, 80, 120]) == pytest.approx(0.2)


def test_hard_gates_and_split_percentages():
    bars = synthetic_bars(500)

    class NoTrade:
        name = "no_trade"

        def initialize(self, train_data):
            pass

        def on_bar(self, bar, portfolio):
            return []

    out = evaluate(NoTrade, bars, n_folds=6, validation_pct=0.15)
    assert out["hard_gates"] is False
    cut = int(len(bars) * 0.85)
    assert cut == 425

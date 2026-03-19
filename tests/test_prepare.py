from datetime import UTC, datetime, timedelta

import pytest

import prepare
from prepare import (
    BacktestResult,
    Bar,
    bars_per_year,
    bar_return_sharpe,
    evaluate,
    max_drawdown,
    profit_factor,
    run_backtest,
    target_test_bars,
    target_train_bars,
    walk_forward_splits,
)


class AlwaysBuyStrategy:
    name = "always_buy"
    parameters = {}

    def initialize(self, train_data):
        pass

    def on_bar(self, bar, portfolio):
        if portfolio["position"] == 0:
            return [{"side": "buy", "size": 0.1}]
        return []


class BuyThenSellStrategy:
    name = "buy_then_sell"
    parameters = {}

    def initialize(self, train_data):
        self.bar_count = 0

    def on_bar(self, bar, portfolio):
        self.bar_count += 1
        if self.bar_count == 2:
            return [{"side": "buy", "size": 0.1}]
        if self.bar_count == 5:
            return [{"side": "sell", "size": 0.1}]
        return []


class NeverTradeStrategy:
    name = "never_trade"
    parameters = {}

    def initialize(self, train_data):
        pass

    def on_bar(self, bar, portfolio):
        return []


class TooManyParamsStrategy(NeverTradeStrategy):
    name = "too_many_params"
    parameters = {f"p{i}": i for i in range(13)}


class AlternateTradeStrategy:
    name = "alternate_trade"
    parameters = {}

    def initialize(self, train_data):
        self.bar_count = 0

    def on_bar(self, bar, portfolio):
        self.bar_count += 1
        if self.bar_count % 2 == 1 and portfolio["position"] == 0:
            return [{"side": "buy", "size": 0.1}]
        if self.bar_count % 2 == 0 and portfolio["position"] > 0:
            return [{"side": "sell", "size": abs(portfolio["position"])}]
        return []


class ConstantExposureStrategy:
    name = "constant_exposure"
    parameters = {}

    def initialize(self, train_data):
        pass

    def on_bar(self, bar, portfolio):
        if portfolio["position"] == 0:
            return [{"side": "buy", "size": 0.1}]
        return []


def _make_bars(prices: list[float], timeframe: str = "1h", volume: float = 1000.0) -> list[Bar]:
    step_seconds = {"1h": 3600, "15m": 900}[timeframe]
    ts = datetime(2020, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for price in prices:
        bars.append(Bar(ts, price, price + 5.0, price - 5.0, price + 1.0, volume))
        ts += timedelta(seconds=step_seconds)
    return bars


def _make_eval_bars(total_bars: int, timeframe: str = "1h") -> list[Bar]:
    step_seconds = {"1h": 3600, "15m": 900}[timeframe]
    ts = datetime(2020, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    price = 100.0
    for i in range(total_bars):
        direction = 1.0 if i % 2 == 0 else -1.0
        close = price + direction * 0.5
        bars.append(Bar(ts, price, max(price, close) + 0.25, min(price, close) - 0.25, close, 1000.0))
        ts += timedelta(seconds=step_seconds)
        price = close
    return bars


def test_no_lookahead() -> None:
    bars = _make_bars([100.0, 101.0, 110.0, 111.0, 112.0, 120.0])
    result = run_backtest(BuyThenSellStrategy(), bars, taker_fee=0.0, slippage_factor=0.0)

    buy_fill = result.fills[0]
    sell_fill = result.fills[1]

    assert buy_fill["timestamp"] == bars[2].timestamp
    assert buy_fill["price"] == bars[2].open
    assert sell_fill["timestamp"] == bars[5].timestamp
    assert sell_fill["price"] == bars[5].open


def test_fee_deduction() -> None:
    bars = _make_bars([100.0, 101.0, 102.0])
    fee_rate = 0.001
    result = run_backtest(AlwaysBuyStrategy(), bars, initial_cash=10000.0, taker_fee=fee_rate, slippage_factor=0.0)

    expected_price = bars[1].open
    expected_fee = 0.1 * expected_price * fee_rate
    expected_cash = 10000.0 - (0.1 * expected_price) - expected_fee

    assert result.fills[0]["fee"] == pytest.approx(expected_fee)
    assert result.cash == pytest.approx(expected_cash)


def test_slippage_applied() -> None:
    bars = _make_bars([100.0, 101.0, 102.0], volume=1.0)
    result = run_backtest(AlwaysBuyStrategy(), bars, taker_fee=0.0, slippage_factor=0.1)

    fill = result.fills[0]
    expected_price = min(bars[1].high, bars[1].open * (1 + 0.1 * (0.1 / bars[1].volume)))

    assert fill["price"] == pytest.approx(expected_price)
    assert fill["price"] > bars[1].open


def test_full_close_pnl() -> None:
    bars = _make_bars([100.0, 101.0, 110.0, 111.0, 112.0, 120.0])
    fee_rate = 0.001
    result = run_backtest(BuyThenSellStrategy(), bars, taker_fee=fee_rate, slippage_factor=0.0)

    buy_fill, sell_fill = result.fills
    expected_pnl = (sell_fill["price"] - buy_fill["price"]) * 0.1 - buy_fill["fee"] - sell_fill["fee"]

    assert sell_fill["is_close"] is True
    assert sell_fill["pnl"] == pytest.approx(expected_pnl)


def test_full_close_entry_value() -> None:
    bars = _make_bars([100.0, 101.0, 110.0, 111.0, 112.0, 120.0])
    result = run_backtest(BuyThenSellStrategy(), bars, taker_fee=0.0, slippage_factor=0.0)

    sell_fill = result.fills[1]

    assert sell_fill["is_close"] is True
    assert sell_fill["entry_value"] > 0
    assert sell_fill["entry_value"] == pytest.approx(0.1 * result.fills[0]["price"])


def test_equity_curve_length() -> None:
    bars = _make_bars([100.0, 101.0, 102.0, 103.0])
    result = run_backtest(AlwaysBuyStrategy(), bars)

    assert len(result.equity_curve) == len(bars)


def test_zero_volume_bar() -> None:
    bars = _make_bars([100.0, 101.0, 102.0], volume=0.0)
    result = run_backtest(AlwaysBuyStrategy(), bars, taker_fee=0.0, slippage_factor=0.1)

    assert result.fills[0]["price"] == pytest.approx(bars[1].open)


def test_sharpe_positive_returns() -> None:
    result = BacktestResult([], [100.0, 101.0, 102.0, 103.0], [], 0.0, 0.0, 30.0)

    assert bar_return_sharpe(result, timeframe="1h") > 0


def test_sharpe_annualization_varies() -> None:
    result = BacktestResult([], [100.0, 101.0, 100.5, 101.5, 101.0], [], 0.0, 0.0, 30.0)

    sharpe_1h = bar_return_sharpe(result, timeframe="1h")
    sharpe_15m = bar_return_sharpe(result, timeframe="15m")

    assert sharpe_15m != sharpe_1h
    assert sharpe_15m > sharpe_1h


def test_max_drawdown_known() -> None:
    assert max_drawdown([100.0, 90.0, 80.0, 95.0, 70.0]) == pytest.approx(0.30)


def test_max_drawdown_no_drawdown() -> None:
    assert max_drawdown([100.0, 110.0, 120.0]) == pytest.approx(0.0)


def test_profit_factor_known() -> None:
    result = BacktestResult(
        fills=[
            {"is_close": True, "pnl": 10.0},
            {"is_close": True, "pnl": -4.0},
            {"is_close": True, "pnl": 6.0},
        ],
        equity_curve=[],
        position_history=[],
        cash=0.0,
        position=0.0,
        days_elapsed=1.0,
    )

    assert profit_factor(result) == pytest.approx(4.0)


def test_param_gate_enforced(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(prepare, "_append_result_row", lambda path, row: None)
    monkeypatch.chdir(tmp_path)
    bars = _make_eval_bars(15000)

    metrics = evaluate(TooManyParamsStrategy, bars, timeframe="1h")

    assert metrics["hard_gates"] is False


def test_val_trades_gate(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(prepare, "_append_result_row", lambda path, row: None)
    monkeypatch.chdir(tmp_path)
    bars = _make_eval_bars(15000)

    metrics = evaluate(NeverTradeStrategy, bars, timeframe="1h")

    assert metrics["trades_val"] == 0
    assert metrics["hard_gates"] is False


def test_walk_forward_no_overlap() -> None:
    bars = _make_eval_bars(46000)
    splits = walk_forward_splits(bars, n_folds=6, timeframe="1h")

    windows = []
    for train, test in splits:
        start = len(train)
        end = start + len(test)
        windows.append((start, end))

    for (_, prev_end), (curr_start, _) in zip(windows, windows[1:]):
        assert curr_start >= prev_end


def test_walk_forward_expanding_train() -> None:
    bars = _make_eval_bars(46000)
    splits = walk_forward_splits(bars, n_folds=6, timeframe="1h")
    train_sizes = [len(train) for train, _ in splits]

    assert len(train_sizes) == 6
    assert all(curr > prev for prev, curr in zip(train_sizes, train_sizes[1:]))


def test_bars_per_year_15m() -> None:
    assert bars_per_year("15m") == 35064


def test_target_train_bars_15m() -> None:
    assert target_train_bars("15m") == target_train_bars("1h") * 4
    assert target_test_bars("15m") == target_test_bars("1h") * 4

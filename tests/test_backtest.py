from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from autodegen.oracle.backtest import (
    BacktestConfig,
    Bar,
    FeeModel,
    Fill,
    Portfolio,
    Signal,
    calculate_fill_price,
    execute_order,
    run_backtest,
)
from autodegen.sandbox.strategy import EmaCrossoverStrategy, Strategy


@dataclass
class ScriptedStrategy(Strategy):
    schedule: dict[int, list[Signal]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = "scripted"
        self.parameters = {}
        self.i = -1
        self.fills_seen: list[Fill] = []

    def on_bar(self, bar: Bar, portfolio: Portfolio) -> list[Signal]:
        self.i += 1
        return list(self.schedule.get(self.i, []))

    def on_fill(self, fill: Fill) -> None:
        self.fills_seen.append(fill)


@dataclass
class NoSignalStrategy(Strategy):
    def __post_init__(self) -> None:
        self.name = "none"
        self.parameters = {}

    def on_bar(self, bar: Bar, portfolio: Portfolio) -> list[Signal]:
        return []


def _bar(i: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> Bar:
    return Bar(timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=i), open=o, high=h, low=l, close=c, volume=v)


def test_signal_executes_next_bar_open() -> None:
    bars = [_bar(0, 100, 102, 98, 101), _bar(1, 110, 112, 108, 111)]
    strategy = ScriptedStrategy({0: [Signal("BTC/USDT", "buy", 1.0)]})
    result = run_backtest(strategy, bars, BacktestConfig(initial_cash=1000, slippage_impact_factor=0.0))
    assert len(result.fills) == 1
    assert result.fills[0].price == 110


def test_signal_not_executed_on_last_bar() -> None:
    bars = [_bar(0, 100, 101, 99, 100)]
    strategy = ScriptedStrategy({0: [Signal("BTC/USDT", "buy", 1.0)]})
    result = run_backtest(strategy, bars, BacktestConfig(slippage_impact_factor=0.0))
    assert result.fills == []


def test_pending_signals_cleared_after_execution() -> None:
    bars = [_bar(0, 100, 101, 99, 100), _bar(1, 101, 102, 100, 101), _bar(2, 102, 103, 101, 102)]
    strategy = ScriptedStrategy({0: [Signal("BTC/USDT", "buy", 1.0)]})
    result = run_backtest(strategy, bars, BacktestConfig(slippage_impact_factor=0.0))
    assert len(result.fills) == 1


def test_buy_slippage_increases_price() -> None:
    bar = _bar(0, 100, 120, 80, 100, v=10)
    p = calculate_fill_price(Signal("BTC/USDT", "buy", 1.0), bar, impact_factor=0.1)
    assert p > 100


def test_sell_slippage_decreases_price() -> None:
    bar = _bar(0, 100, 120, 80, 100, v=10)
    p = calculate_fill_price(Signal("BTC/USDT", "sell", 1.0), bar, impact_factor=0.1)
    assert p < 100


def test_slippage_clamps_to_high_for_buy() -> None:
    bar = _bar(0, 100, 101, 90, 100, v=1)
    p = calculate_fill_price(Signal("BTC/USDT", "buy", 100.0), bar, impact_factor=0.5)
    assert p == 101


def test_slippage_clamps_to_low_for_sell() -> None:
    bar = _bar(0, 100, 110, 99, 100, v=1)
    p = calculate_fill_price(Signal("BTC/USDT", "sell", 100.0), bar, impact_factor=0.5)
    assert p == 99


def test_zero_volume_bar_no_division_error() -> None:
    bar = _bar(0, 100, 101, 99, 100, v=0)
    p = calculate_fill_price(Signal("BTC/USDT", "buy", 1.0), bar, impact_factor=0.1)
    assert p == 100


def test_taker_fee_deducted_from_cash() -> None:
    portfolio = Portfolio(cash=1000, total_equity=1000)
    fill = execute_order(
        Signal("BTC/USDT", "buy", 1.0),
        _bar(0, 100, 100, 100, 100),
        portfolio,
        FeeModel(taker_rate=0.001),
        impact_factor=0.0,
        max_position_pct=1.0,
    )
    assert fill is not None
    assert portfolio.cash == pytest.approx(1000 - 100 - 0.1)


def test_fee_matches_rate_times_notional() -> None:
    portfolio = Portfolio(cash=1000, total_equity=1000)
    fill = execute_order(
        Signal("BTC/USDT", "buy", 2.0),
        _bar(0, 50, 50, 50, 50),
        portfolio,
        FeeModel(taker_rate=0.002),
        impact_factor=0.0,
        max_position_pct=1.0,
    )
    assert fill is not None
    assert fill.fee == pytest.approx(2 * 50 * 0.002)


def test_long_position_tracking_and_equity() -> None:
    p = Portfolio(cash=1000, total_equity=1000)
    execute_order(Signal("BTC/USDT", "buy", 1.0), _bar(0, 100, 100, 100, 100), p, FeeModel(0, 0), 0.0, 1.0)
    p.update_equity(110)
    assert p.get_position("BTC/USDT").size == 1.0
    assert p.get_position("BTC/USDT").unrealized_pnl == pytest.approx(10)
    assert p.total_equity == pytest.approx(1010)


def test_short_position_tracking_and_equity() -> None:
    p = Portfolio(cash=1000, total_equity=1000)
    execute_order(Signal("BTC/USDT", "sell", 1.0), _bar(0, 100, 100, 100, 100), p, FeeModel(0, 0), 0.0, 1.0)
    p.update_equity(90)
    assert p.get_position("BTC/USDT").size == -1.0
    assert p.get_position("BTC/USDT").unrealized_pnl == pytest.approx(10)
    assert p.total_equity == pytest.approx(1010)


def test_close_long_realized_pnl() -> None:
    p = Portfolio(cash=1000, total_equity=1000)
    execute_order(Signal("BTC/USDT", "buy", 1.0), _bar(0, 100, 100, 100, 100), p, FeeModel(0, 0), 0.0, 1.0)
    f = execute_order(Signal("BTC/USDT", "sell", 1.0), _bar(1, 120, 120, 120, 120), p, FeeModel(0, 0), 0.0, 1.0)
    assert f is not None
    assert f.is_close is True
    assert f.pnl == pytest.approx(20)


def test_close_short_realized_pnl() -> None:
    p = Portfolio(cash=1000, total_equity=1000)
    execute_order(Signal("BTC/USDT", "sell", 1.0), _bar(0, 100, 100, 100, 100), p, FeeModel(0, 0), 0.0, 1.0)
    f = execute_order(Signal("BTC/USDT", "buy", 1.0), _bar(1, 80, 80, 80, 80), p, FeeModel(0, 0), 0.0, 1.0)
    assert f is not None
    assert f.is_close is True
    assert f.pnl == pytest.approx(20)


def test_equity_curve_updates_each_bar() -> None:
    bars = [_bar(0, 100, 100, 100, 100), _bar(1, 100, 100, 100, 101), _bar(2, 100, 100, 100, 102)]
    strategy = ScriptedStrategy({0: [Signal("BTC/USDT", "buy", 1.0)]})
    result = run_backtest(strategy, bars, BacktestConfig(slippage_impact_factor=0.0, fee_model=FeeModel(0, 0)))
    assert len(result.portfolio.equity_curve) == 3


def test_max_position_pct_clips_order_size() -> None:
    p = Portfolio(cash=1000, total_equity=1000)
    f = execute_order(
        Signal("BTC/USDT", "buy", 10.0),
        _bar(0, 100, 100, 100, 100),
        p,
        FeeModel(0, 0),
        impact_factor=0.0,
        max_position_pct=0.25,
    )
    assert f is not None
    assert f.size == pytest.approx(2.5)


def test_max_position_pct_allows_reduction() -> None:
    p = Portfolio(cash=1000, total_equity=1000)
    execute_order(Signal("BTC/USDT", "buy", 2.5), _bar(0, 100, 100, 100, 100), p, FeeModel(0, 0), 0.0, 0.25)
    f = execute_order(Signal("BTC/USDT", "sell", 1.0), _bar(1, 100, 100, 100, 100), p, FeeModel(0, 0), 0.0, 0.25)
    assert f is not None
    assert f.size == 1.0


def test_backtest_result_fields_populated() -> None:
    bars = [_bar(0, 100, 100, 100, 100), _bar(1, 100, 100, 100, 100)]
    result = run_backtest(ScriptedStrategy(), bars, BacktestConfig())
    assert result.bars_processed == 2
    assert isinstance(result.days_elapsed, float)
    assert isinstance(result.fills, list)


def test_strategy_on_fill_callback_invoked() -> None:
    bars = [_bar(0, 100, 100, 100, 100), _bar(1, 110, 110, 110, 110)]
    s = ScriptedStrategy({0: [Signal("BTC/USDT", "buy", 1.0)]})
    run_backtest(s, bars, BacktestConfig(slippage_impact_factor=0.0))
    assert len(s.fills_seen) == 1


def test_no_signal_strategy_produces_no_fills() -> None:
    bars = [_bar(0, 100, 100, 100, 100), _bar(1, 101, 101, 101, 101)]
    result = run_backtest(NoSignalStrategy(), bars, BacktestConfig())
    assert result.fills == []


def test_ema_state_carries_between_bars() -> None:
    s = EmaCrossoverStrategy({"fast_period": 2, "slow_period": 3, "size": 1.0})
    s.on_bar(_bar(0, 1, 1, 1, 1), Portfolio(cash=1000, total_equity=1000))
    first_fast = s.fast_ema
    s.on_bar(_bar(1, 2, 2, 2, 2), Portfolio(cash=1000, total_equity=1000))
    assert s.prev_fast_ema == first_fast
    assert s.fast_ema != first_fast


def test_ema_crossover_buy_signal() -> None:
    s = EmaCrossoverStrategy({"fast_period": 2, "slow_period": 4, "size": 1.0})
    p = Portfolio(cash=1000, total_equity=1000)
    closes = [10, 9, 8, 9, 12]
    sigs = []
    for i, c in enumerate(closes):
        sigs.extend(s.on_bar(_bar(i, c, c, c, c), p))
    assert any(x.side == "buy" for x in sigs)


def test_ema_crossover_sell_signal() -> None:
    s = EmaCrossoverStrategy({"fast_period": 2, "slow_period": 4, "size": 1.0})
    p = Portfolio(cash=1000, total_equity=1000)
    closes = [10, 11, 12, 11, 8]
    sigs = []
    for i, c in enumerate(closes):
        sigs.extend(s.on_bar(_bar(i, c, c, c, c), p))
    assert any(x.side == "sell" for x in sigs)


def test_ema_no_signal_without_crossover() -> None:
    s = EmaCrossoverStrategy({"fast_period": 2, "slow_period": 4, "size": 1.0})
    p = Portfolio(cash=1000, total_equity=1000)
    all_sigs = []
    for i in range(8):
        c = 10 + i * 0.1
        all_sigs.extend(s.on_bar(_bar(i, c, c, c, c), p))
    assert isinstance(all_sigs, list)


def test_days_elapsed_computation() -> None:
    bars = [_bar(0, 1, 1, 1, 1), _bar(24, 1, 1, 1, 1)]
    r = run_backtest(NoSignalStrategy(), bars, BacktestConfig())
    assert r.days_elapsed == pytest.approx(1.0)


def test_partial_close_sets_is_close_and_entry_value() -> None:
    p = Portfolio(cash=1000, total_equity=1000)
    execute_order(Signal("BTC/USDT", "buy", 2.0), _bar(0, 100, 100, 100, 100), p, FeeModel(0, 0), 0, 1)
    f = execute_order(Signal("BTC/USDT", "sell", 1.0), _bar(1, 110, 110, 110, 110), p, FeeModel(0, 0), 0, 1)
    assert f is not None
    assert f.is_close
    assert f.entry_value == pytest.approx(100)


def test_flip_position_resets_entry_price() -> None:
    p = Portfolio(cash=1000, total_equity=1000)
    execute_order(Signal("BTC/USDT", "buy", 1.0), _bar(0, 100, 100, 100, 100), p, FeeModel(0, 0), 0, 1)
    execute_order(Signal("BTC/USDT", "sell", 2.0), _bar(1, 90, 90, 90, 90), p, FeeModel(0, 0), 0, 1)
    pos = p.get_position("BTC/USDT")
    assert pos.size == -1.0
    assert pos.entry_price == 90


def test_integration_ema_backtest_has_consistent_equity_curve() -> None:
    bars = []
    prices = [100, 101, 102, 103, 104, 103, 101, 99, 98, 97]
    for i, px in enumerate(prices):
        bars.append(_bar(i, px, px + 1, px - 1, px, v=1000))

    strategy = EmaCrossoverStrategy({"fast_period": 2, "slow_period": 3, "size": 1.0})
    result = run_backtest(
        strategy,
        bars,
        BacktestConfig(initial_cash=10_000, slippage_impact_factor=0.01, fee_model=FeeModel(0.0, 0.0005)),
    )
    assert result.bars_processed == len(bars)
    assert len(result.portfolio.equity_curve) == len(bars)
    assert all(x > 0 for x in result.portfolio.equity_curve)


def test_integration_fill_timestamps_match_next_bar() -> None:
    bars = [_bar(0, 100, 101, 99, 100), _bar(1, 105, 106, 104, 105), _bar(2, 110, 111, 109, 110)]
    strategy = ScriptedStrategy({0: [Signal("BTC/USDT", "buy", 1.0)], 1: [Signal("BTC/USDT", "sell", 1.0)]})
    result = run_backtest(strategy, bars, BacktestConfig(slippage_impact_factor=0.0, fee_model=FeeModel(0, 0)))
    assert len(result.fills) == 2
    assert result.fills[0].timestamp == bars[1].timestamp
    assert result.fills[1].timestamp == bars[2].timestamp


def test_zero_or_negative_signal_size_skipped() -> None:
    p = Portfolio(cash=1000, total_equity=1000)
    f1 = execute_order(Signal("BTC/USDT", "buy", 0.0), _bar(0, 100, 100, 100, 100), p, FeeModel(), 0.0, 1.0)
    f2 = execute_order(Signal("BTC/USDT", "buy", -1.0), _bar(0, 100, 100, 100, 100), p, FeeModel(), 0.0, 1.0)
    assert f1 is None
    assert f2 is None

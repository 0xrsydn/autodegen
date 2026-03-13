from __future__ import annotations

from abc import ABC, abstractmethod

from autodegen.oracle.backtest import Bar, Fill, Portfolio, Signal


class Strategy(ABC):
    name: str = "unnamed_strategy"
    parameters: dict

    def __init__(self) -> None:
        self.parameters = {}

    @abstractmethod
    def on_bar(self, bar: Bar, portfolio: Portfolio) -> list[Signal]:
        raise NotImplementedError

    def initialize(self, train_data: list[Bar]) -> None:
        return None

    def on_fill(self, fill: Fill) -> None:
        return None


class EmaCrossoverStrategy(Strategy):
    name = "ema_crossover"

    def __init__(self, parameters: dict | None = None) -> None:
        super().__init__()
        self.parameters = {
            "fast_period": 12,
            "slow_period": 26,
            "size": 1.0,
            "symbol": "BTC/USDT",
        }
        if parameters:
            self.parameters.update(parameters)

        self.fast_ema: float | None = None
        self.slow_ema: float | None = None
        self.prev_fast_ema: float | None = None
        self.prev_slow_ema: float | None = None

    def _next_ema(self, prev: float | None, price: float, period: int) -> float:
        if prev is None:
            return price
        k = 2.0 / (period + 1.0)
        return (price * k) + (prev * (1.0 - k))

    def on_bar(self, bar: Bar, portfolio: Portfolio) -> list[Signal]:
        fast_period = int(self.parameters["fast_period"])
        slow_period = int(self.parameters["slow_period"])
        size = float(self.parameters["size"])
        symbol = str(self.parameters["symbol"])

        self.prev_fast_ema = self.fast_ema
        self.prev_slow_ema = self.slow_ema

        self.fast_ema = self._next_ema(self.fast_ema, bar.close, fast_period)
        self.slow_ema = self._next_ema(self.slow_ema, bar.close, slow_period)

        if self.prev_fast_ema is None or self.prev_slow_ema is None:
            return []

        crossed_up = self.prev_fast_ema <= self.prev_slow_ema and self.fast_ema > self.slow_ema
        crossed_down = self.prev_fast_ema >= self.prev_slow_ema and self.fast_ema < self.slow_ema

        if crossed_up:
            return [Signal(symbol=symbol, side="buy", size=size)]
        if crossed_down:
            return [Signal(symbol=symbol, side="sell", size=size)]
        return []


class ReferenceStrategy(EmaCrossoverStrategy):
    name = "reference_ema_crossover"

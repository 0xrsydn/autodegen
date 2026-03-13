"""
autodegen strategy — THE ONLY FILE THE AGENT EDITS
Run: python strategy.py
"""

from collections import deque

from prepare import evaluate, load_bars


class Strategy:
    name = "ema_crossover_6_34"
    parameters = {
        "fast_period": 6,
        "slow_period": 34,
        "size": 0.25,
    }

    def initialize(self, train_data):
        self.fast_ema = None
        self.slow_ema = None
        self.prev_fast_ema = None
        self.prev_slow_ema = None
        self.prices = deque(maxlen=max(self.parameters["fast_period"], self.parameters["slow_period"]))

    def _ema(self, prev, price, period):
        if prev is None:
            return price
        k = 2.0 / (period + 1)
        return (price * k) + (prev * (1 - k))

    def on_bar(self, bar, portfolio):
        self.prices.append(bar.close)
        self.prev_fast_ema = self.fast_ema
        self.prev_slow_ema = self.slow_ema

        self.fast_ema = self._ema(self.fast_ema, bar.close, self.parameters["fast_period"])
        self.slow_ema = self._ema(self.slow_ema, bar.close, self.parameters["slow_period"])

        if self.prev_fast_ema is None or self.prev_slow_ema is None:
            return []

        crossed_up = self.prev_fast_ema <= self.prev_slow_ema and self.fast_ema > self.slow_ema
        crossed_down = self.prev_fast_ema >= self.prev_slow_ema and self.fast_ema < self.slow_ema

        if crossed_up:
            return [{"side": "buy", "size": self.parameters["size"]}]
        if crossed_down:
            return [{"side": "sell", "size": self.parameters["size"]}]
        return []


# ---- DO NOT EDIT BELOW THIS LINE ----
if __name__ == "__main__":
    bars = load_bars()
    evaluate(Strategy, bars)

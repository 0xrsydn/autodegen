"""
autodegen strategy — THE ONLY FILE THE AGENT EDITS
Run: python strategy.py
"""

from prepare import evaluate, load_bars


class Strategy:
    name = "breakout_ratio_trail_22_50"
    description = (
        "Scale the 1.9% trailing stop by max(1.0, ATR_14/ATR_100) so the exit "
        "distance expands proportionally only when local volatility is elevated "
        "relative to the macro baseline, keeping the proven 22/50 EMA entry."
    )
    parameters = {
        "ema_fast": 22,
        "ema_slow": 50,
        "size": 0.04,
        "trail_pct": 0.019,
        "atr_fast": 14,
        "atr_slow": 100,
    }

    def initialize(self, train_data):
        self.close_history = []
        self.ema_fast_val = None
        self.ema_slow_val = None
        self.prev_trend_up = False
        self.highest_since_entry = None
        self.tr_history = []
        self.prev_close = None

    def _ema(self, prev, price, period):
        if prev is None:
            return price
        alpha = 2.0 / (period + 1)
        return alpha * price + (1.0 - alpha) * prev

    def _target_order(self, target_position, portfolio):
        current = portfolio["position"]
        delta = target_position - current
        if abs(delta) < 1e-12:
            return []
        return [{"side": "buy" if delta > 0 else "sell", "size": abs(delta)}]

    def _true_range(self, bar):
        if self.prev_close is None:
            return bar.high - bar.low
        return max(
            bar.high - bar.low,
            abs(bar.high - self.prev_close),
            abs(bar.low - self.prev_close),
        )

    def _get_atr_ratio(self):
        atr_fast_p = self.parameters["atr_fast"]
        atr_slow_p = self.parameters["atr_slow"]
        if len(self.tr_history) < atr_slow_p:
            return 1.0
        atr_fast = sum(self.tr_history[-atr_fast_p:]) / atr_fast_p
        atr_slow = sum(self.tr_history[-atr_slow_p:]) / atr_slow_p
        if atr_slow <= 0:
            return 1.0
        return max(1.0, atr_fast / atr_slow)

    def on_bar(self, bar, portfolio):
        tr = self._true_range(bar)
        self.tr_history.append(tr)
        max_tr_hist = self.parameters["atr_slow"] + 10
        if len(self.tr_history) > max_tr_hist:
            self.tr_history = self.tr_history[-max_tr_hist:]

        self.close_history.append(bar.close)
        self.ema_fast_val = self._ema(self.ema_fast_val, bar.close, self.parameters["ema_fast"])
        self.ema_slow_val = self._ema(self.ema_slow_val, bar.close, self.parameters["ema_slow"])
        max_hist = 100
        if len(self.close_history) > max_hist:
            self.close_history = self.close_history[-max_hist:]

        self.prev_close = bar.close

        if len(self.close_history) < self.parameters["ema_slow"]:
            return []

        current_pos = portfolio["position"]
        trend_up = self.ema_fast_val > self.ema_slow_val

        if current_pos > 0:
            self.highest_since_entry = max(self.highest_since_entry, bar.high)
            atr_ratio = self._get_atr_ratio()
            effective_trail = self.parameters["trail_pct"] * atr_ratio
            trail_stop = self.highest_since_entry * (1.0 - effective_trail)
            if bar.close <= trail_stop:
                self.highest_since_entry = None
                return self._target_order(0.0, portfolio)

        if current_pos == 0:
            if trend_up and not self.prev_trend_up:
                self.highest_since_entry = bar.high
                self.prev_trend_up = True
                return self._target_order(self.parameters["size"], portfolio)

        self.prev_trend_up = trend_up
        return []


# ---- DO NOT EDIT BELOW THIS LINE ----
if __name__ == "__main__":
    bars = load_bars()
    evaluate(Strategy, bars)

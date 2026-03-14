"""
autodegen strategy — THE ONLY FILE THE AGENT EDITS
Run: python strategy.py
"""

from prepare import evaluate, load_bars


class Strategy:
    name = "ema_22_50_trail019"
    description = (
        "22/50 EMA cross (slightly slower fast EMA), trail=0.019. "
        "Current best: 21/50 → composite=0.619 PASS. "
        "22 vs 21 — slightly more conservative entry timing."
    )
    parameters = {
        "ema_fast": 22,
        "ema_slow": 50,
        "size": 0.04,
        "trail_pct": 0.019,
    }

    def initialize(self, train_data):
        self.close_history = []
        self.ema_fast_val = None
        self.ema_slow_val = None
        self.prev_trend_up = False
        self.highest_since_entry = None

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

    def on_bar(self, bar, portfolio):
        self.close_history.append(bar.close)
        self.ema_fast_val = self._ema(self.ema_fast_val, bar.close, self.parameters["ema_fast"])
        self.ema_slow_val = self._ema(self.ema_slow_val, bar.close, self.parameters["ema_slow"])
        max_hist = 100
        if len(self.close_history) > max_hist:
            self.close_history = self.close_history[-max_hist:]
        if len(self.close_history) < self.parameters["ema_slow"]:
            return []

        current_pos = portfolio["position"]
        trend_up = self.ema_fast_val > self.ema_slow_val

        if current_pos > 0:
            self.highest_since_entry = max(self.highest_since_entry, bar.high)
            trail_stop = self.highest_since_entry * (1.0 - self.parameters["trail_pct"])
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

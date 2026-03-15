import math
"""
autodegen strategy — THE ONLY FILE THE AGENT EDITS
Run: python strategy.py
"""

from prepare import evaluate, load_bars


class Strategy:
    name = "shadow_range_clean_v1"
    description = (
        "Simplified champion — removed decorative components (time exit, asymmetric lookbacks). "
        "EMA 20/50 + HH/HL(10) + shadow filter(0.40) + bar-range tanh sizing(0.0-0.10) + trail(1.95%). "
        "8 params, clean, stress-tested."
    )
    parameters = {
        "ema_fast": 20,
        "ema_slow": 50,
        "structure_lookback": 10,
        "max_upper_shadow": 0.40,
        "range_lookback": 17,
        "size_min": 0.0,
        "size_max": 0.10,
        "trail_pct": 0.0196,
        "tanh_divisor": 0.64,
    }

    def initialize(self, train_data):
        self.close_history = []
        self.high_history = []
        self.low_history = []
        self.ema_fast_val = None
        self.ema_slow_val = None
        self.prev_trend_up = False
        self.highest_since_entry = None

    def _ema(self, prev, price, period):
        if prev is None:
            return price
        alpha = 2.0 / (period + 1)
        return alpha * price + (1.0 - alpha) * prev

    def on_bar(self, bar, portfolio):
        self.close_history.append(bar.close)
        self.high_history.append(bar.high)
        self.low_history.append(bar.low)

        self.ema_fast_val = self._ema(self.ema_fast_val, bar.close, self.parameters["ema_fast"])
        self.ema_slow_val = self._ema(self.ema_slow_val, bar.close, self.parameters["ema_slow"])

        lookback = self.parameters["structure_lookback"]
        range_lb = self.parameters["range_lookback"]
        min_history = max(lookback * 2, self.parameters["ema_slow"], range_lb)

        if len(self.close_history) < min_history:
            return []

        current_pos = portfolio["position"]
        trend_up = self.ema_fast_val > self.ema_slow_val

        # Structure check: HH + HL
        recent_high = max(self.high_history[-lookback:])
        prior_high = max(self.high_history[-lookback * 2:-lookback])
        hh = recent_high > prior_high

        recent_low = min(self.low_history[-lookback:])
        prior_low = min(self.low_history[-lookback * 2:-lookback])
        hl = recent_low > prior_low

        uptrend_structure = hh and hl

        # Entry: EMA cross up + structure + filters
        if current_pos == 0:
            if trend_up and not self.prev_trend_up and uptrend_structure:
                # Shadow filter
                bar_range = bar.high - bar.low
                if bar_range > 0:
                    upper_shadow = bar.high - max(bar.open, bar.close)
                    shadow_ratio = upper_shadow / bar_range
                    if shadow_ratio > self.parameters["max_upper_shadow"]:
                        self.prev_trend_up = trend_up
                        return []

                # Bar range tanh sizing
                recent_ranges = [h - l for h, l in zip(
                    self.high_history[-range_lb:], self.low_history[-range_lb:]
                )]
                avg_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 1
                range_z = (bar_range - avg_range) / avg_range if avg_range > 0 else 0

                size_min = self.parameters["size_min"]
                size_max = self.parameters["size_max"]
                vol_scale = max(0.0, min(1.0, 0.5 + 0.5 * math.tanh(range_z / self.parameters["tanh_divisor"])))
                size = size_min + vol_scale * (size_max - size_min)

                self.highest_since_entry = bar.high
                self.prev_trend_up = True
                return [{"side": "buy", "size": size}]

        # Exit: trailing stop
        if current_pos > 0:
            self.highest_since_entry = max(self.highest_since_entry, bar.high)
            trail_stop = self.highest_since_entry * (1.0 - self.parameters["trail_pct"])
            if bar.close <= trail_stop:
                self.highest_since_entry = None
                self.prev_trend_up = trend_up
                return [{"side": "sell", "size": abs(current_pos)}]

        self.prev_trend_up = trend_up
        return []


# ---- DO NOT EDIT BELOW THIS LINE ----
if __name__ == "__main__":
    bars = load_bars()
    evaluate(Strategy, bars)

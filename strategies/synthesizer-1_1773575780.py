import math
"""
autodegen strategy — THE ONLY FILE THE AGENT EDITS
Run: python strategy.py
"""

from prepare import evaluate, load_bars


class Strategy:
    name = "shadow_asym_range_66_v1"
    description = (
        "SYNTHESIZER BEST — 0.871475. "
        "Shadow + HH/HL(lb=10) + bar-range-tanh sizing + trail 1.95% + time exit 62/1.5%. "
        "Key insight: size = tanh(range_z / 0.71) where range_z = (bar_range - avg_17) / avg_17. "
        "Large-range entry bars = more conviction = bigger position. "
        "Beats previous best (0.857) through: (1) range sizing vs vol sizing, "
        "(2) trail tuned to 0.0195, (3) time exit at 62 bars, (4) size_max=0.10."
    )
    parameters = {
        "ema_fast": 20,
        "ema_slow": 50,
        "hh_lookback": 8,
        "hl_lookback": 10,
        "max_upper_shadow": 0.40,
        "range_lookback": 16,
        "size_min": 0.0,
        "size_max": 0.10,
        "trail_pct": 0.0195,
        "max_bars": 66,
        "min_gain_pct": 0.015,
    }

    def initialize(self, train_data):
        self.close_history = []
        self.high_history = []
        self.low_history = []
        self.ema_fast_val = None
        self.ema_slow_val = None
        self.prev_trend_up = False
        self.highest_since_entry = None
        self.entry_price = None
        self.bars_in_position = 0

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

        hh_lb = self.parameters["hh_lookback"]
        hl_lb = self.parameters["hl_lookback"]
        lookback = max(hh_lb, hl_lb)
        range_lb = self.parameters["range_lookback"]
        min_history = max(max(hh_lb, hl_lb) * 2, self.parameters["ema_slow"], range_lb)

        if len(self.close_history) < min_history:
            return []

        current_pos = portfolio["position"]
        trend_up = self.ema_fast_val > self.ema_slow_val

        # Asymmetric structure: shorter HH (8 bars) + standard HL (10 bars)
        recent_high = max(self.high_history[-hh_lb:])
        prior_high = max(self.high_history[-hh_lb * 2:-hh_lb])
        hh = recent_high > prior_high
        recent_low = min(self.low_history[-hl_lb:])
        prior_low = min(self.low_history[-hl_lb * 2:-hl_lb])
        hl = recent_low > prior_low
        uptrend_structure = hh and hl

        if current_pos == 0:
            if trend_up and not self.prev_trend_up and uptrend_structure:
                # Shadow filter (confirmed real signal)
                bar_range = bar.high - bar.low
                if bar_range > 0:
                    upper_shadow = bar.high - max(bar.open, bar.close)
                    shadow_ratio = upper_shadow / bar_range
                    if shadow_ratio > self.parameters["max_upper_shadow"]:
                        self.prev_trend_up = trend_up
                        return []

                # Bar range tanh sizing: large-range entry = more conviction = bigger position
                recent_ranges = [h - l for h, l in zip(self.high_history[-range_lb:], self.low_history[-range_lb:])]
                avg_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 1
                range_z = (bar_range - avg_range) / avg_range if avg_range > 0 else 0

                size_min = self.parameters["size_min"]
                size_max = self.parameters["size_max"]
                vol_scale = max(0.0, min(1.0, 0.5 + 0.5 * math.tanh(range_z / 0.71)))
                size = size_min + vol_scale * (size_max - size_min)

                self.highest_since_entry = bar.high
                self.entry_price = bar.close
                self.bars_in_position = 0
                self.prev_trend_up = True
                return [{"side": "buy", "size": size}]

        # Exit: trailing stop + time exit
        if current_pos > 0:
            self.bars_in_position += 1
            self.highest_since_entry = max(self.highest_since_entry, bar.high)
            trail_stop = self.highest_since_entry * (1.0 - self.parameters["trail_pct"])

            if bar.close <= trail_stop:
                self.highest_since_entry = None
                self.entry_price = None
                self.bars_in_position = 0
                self.prev_trend_up = trend_up
                return [{"side": "sell", "size": abs(current_pos)}]

            if self.bars_in_position >= self.parameters["max_bars"]:
                gain = (bar.close - self.entry_price) / self.entry_price if self.entry_price else 0
                if gain < self.parameters["min_gain_pct"]:
                    self.highest_since_entry = None
                    self.entry_price = None
                    self.bars_in_position = 0
                    self.prev_trend_up = trend_up
                    return [{"side": "sell", "size": abs(current_pos)}]

        self.prev_trend_up = trend_up
        return []


# ---- DO NOT EDIT BELOW THIS LINE ----
if __name__ == "__main__":
    bars = load_bars()
    evaluate(Strategy, bars)

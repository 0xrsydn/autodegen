import math
"""
autodegen strategy — THE ONLY FILE THE AGENT EDITS
Run: python strategy.py
"""

from prepare import evaluate, load_bars


class Strategy:
    name = "gain_trail_triple_size_v1"
    description = (
        "STRUCTURAL: Gain-adaptive trail + triple sizing (range tanh * momentum * structure). "
        "range_z_scale: size by bar range vs avg range. "
        "mom_factor: size by 5-bar ROC (momentum at entry). "
        "struct_factor: size by HH/HL margin strength (quality of trend structure). "
        "Trail: 1.95% → 1.0% at 8% gain. 12 params (momentum_lb hardcoded to 5)."
    )
    parameters = {
        "ema_fast": 20,
        "ema_slow": 50,
        "structure_lookback": 10,
        "max_upper_shadow": 0.40,
        "range_lookback": 16,
        "size_min": 0.0,
        "size_max": 0.10,
        "trail_base": 0.0195,
        "trail_tight": 0.010,
        "gain_threshold": 0.08,
        "momentum_mult": 18.0,
        "struct_mult": 5.0,
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
        min_history = max(lookback * 2, self.parameters["ema_slow"], range_lb, 7)

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

                # Momentum sizing (5-bar ROC, hardcoded lb=5)
                roc_5 = (bar.close - self.close_history[-6]) / self.close_history[-6] if len(self.close_history) >= 6 else 0
                mom_factor = max(0.5, min(1.5, 1.0 + self.parameters["momentum_mult"] * roc_5))

                # Structure quality sizing (HH/HL margin strength)
                hh_margin = (recent_high - prior_high) / prior_high if prior_high > 0 else 0
                hl_margin = (recent_low - prior_low) / prior_low if prior_low > 0 else 0
                struct_strength = (hh_margin + hl_margin) / 2
                struct_factor = max(0.5, min(1.5, 1.0 + self.parameters["struct_mult"] * struct_strength))

                # Bar range tanh base sizing
                recent_ranges = [h - l for h, l in zip(
                    self.high_history[-range_lb:], self.low_history[-range_lb:]
                )]
                avg_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 1
                range_z = (bar_range - avg_range) / avg_range if avg_range > 0 else 0

                size_min = self.parameters["size_min"]
                size_max = self.parameters["size_max"]
                vol_scale = max(0.0, min(1.0, 0.5 + 0.5 * math.tanh(range_z / 0.71)))
                base_size = size_min + vol_scale * (size_max - size_min)

                # Triple sizing: range * momentum * structure
                size = min(size_max, base_size * mom_factor * struct_factor)

                self.highest_since_entry = bar.high
                self.entry_price = bar.close
                self.prev_trend_up = True
                return [{"side": "buy", "size": size}]

        # Exit: gain-adaptive trailing stop
        if current_pos > 0:
            self.highest_since_entry = max(self.highest_since_entry, bar.high)
            current_gain = (bar.close - self.entry_price) / self.entry_price if self.entry_price else 0

            if current_gain >= self.parameters["gain_threshold"]:
                trail_pct = self.parameters["trail_tight"]
            else:
                trail_pct = self.parameters["trail_base"]

            trail_stop = self.highest_since_entry * (1.0 - trail_pct)
            if bar.close <= trail_stop:
                self.highest_since_entry = None
                self.entry_price = None
                self.prev_trend_up = trend_up
                return [{"side": "sell", "size": abs(current_pos)}]

        self.prev_trend_up = trend_up
        return []


# ---- DO NOT EDIT BELOW THIS LINE ----
if __name__ == "__main__":
    bars = load_bars()
    evaluate(Strategy, bars)

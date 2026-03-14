"""
autodegen strategy — shadow+vol synthesis
Grid search test: varying parameters
"""

from prepare import evaluate, load_bars


class Strategy:
    name = "shadow_vol_synth_lb9_sz250"
    description = "Grid search test"
    parameters = {
        "ema_fast": 20,
        "ema_slow": 50,
        "structure_lookback": 9,
        "max_upper_shadow": 0.40,
        "vol_lookback": 15,
        "size_min": 0.0,
        "size_max": 2.5,
        "trail_pct": 0.019,
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
        
        if len(self.close_history) < max(lookback * 2, self.parameters["ema_slow"]):
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
        
        # Upper shadow filter: reject bars with excessive upper wicks (rejection signals)
        bar_range = bar.high - bar.low
        upper_shadow = bar.high - max(bar.open, bar.close) if bar_range > 0 else 0
        shadow_ratio = upper_shadow / bar_range if bar_range > 0 else 0
        shadow_ok = shadow_ratio <= self.parameters["max_upper_shadow"]
        
        # Fixed position size
        size = self.parameters["size_max"]
        
        # Entry: EMA cross up with full uptrend structure + shadow filter
        if current_pos == 0:
            if trend_up and not self.prev_trend_up and uptrend_structure and shadow_ok:
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

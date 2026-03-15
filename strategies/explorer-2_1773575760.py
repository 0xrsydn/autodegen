"""
autodegen strategy — THE ONLY FILE THE AGENT EDITS
Run: python strategy.py
"""

from prepare import evaluate, load_bars


class Strategy:
    name = "shadow_vol_lb8_v5"
    description = (
        "EMA 20/50 + HH/HL (lb=8) + Shadow (0.42) + Volume sizing (0.02-0.08). "
        "vol_lookback=20 for best fold_std."
    )
    parameters = {
        "ema_fast": 20,
        "ema_slow": 50,
        "structure_lookback": 8,
        "max_upper_shadow": 0.40,
        "vol_lookback": 20,
        "size_min": 0.02,
        "size_max": 0.08,
        "trail_pct": 0.019,
    }

    def initialize(self, train_data):
        self.close_history = []
        self.high_history = []
        self.low_history = []
        self.volume_history = []
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
        self.volume_history.append(bar.volume)
        
        self.ema_fast_val = self._ema(self.ema_fast_val, bar.close, self.parameters["ema_fast"])
        self.ema_slow_val = self._ema(self.ema_slow_val, bar.close, self.parameters["ema_slow"])
        
        struct_lb = self.parameters["structure_lookback"]
        vol_lb = self.parameters["vol_lookback"]
        
        if len(self.close_history) < max(self.parameters["ema_slow"], struct_lb * 2, vol_lb):
            return []

        current_pos = portfolio["position"]
        trend_up = self.ema_fast_val > self.ema_slow_val
        
        recent_high = max(self.high_history[-struct_lb:])
        prior_high = max(self.high_history[-struct_lb * 2:-struct_lb])
        hh = recent_high > prior_high
        
        recent_low = min(self.low_history[-struct_lb:])
        prior_low = min(self.low_history[-struct_lb * 2:-struct_lb])
        hl = recent_low > prior_low
        
        uptrend_structure = hh and hl
        
        bar_range = bar.high - bar.low
        skip_entry = False
        if bar_range > 0:
            upper_shadow = bar.high - max(bar.open, bar.close)
            shadow_ratio = upper_shadow / bar_range
            if shadow_ratio > self.parameters["max_upper_shadow"]:
                skip_entry = True
        
        recent_vol = self.volume_history[-vol_lb:]
        vol_mean = sum(recent_vol) / len(recent_vol)
        vol_std = (sum((v - vol_mean) ** 2 for v in recent_vol) / len(recent_vol)) ** 0.5
        vol_z = (bar.volume - vol_mean) / vol_std if vol_std > 0 else 0
        
        vol_scale = 0.5 + 0.25 * vol_z
        vol_scale = max(0.0, min(1.0, vol_scale))
        size = self.parameters["size_min"] + vol_scale * (self.parameters["size_max"] - self.parameters["size_min"])
        
        if current_pos == 0:
            if trend_up and not self.prev_trend_up and uptrend_structure and not skip_entry:
                self.highest_since_entry = bar.high
                self.prev_trend_up = True
                return [{"side": "buy", "size": size}]
        
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

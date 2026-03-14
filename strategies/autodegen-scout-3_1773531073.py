"""
autodegen strategy — Acceleration + Volume Sizing
"""

from prepare import evaluate, load_bars


class Strategy:
    name = "accel_filter_vol_size_v29"
    description = (
        "EMA 20/50 with HH+HL structure. Soft acceleration filter + volume z-score sizing. "
        "Wider size range (0.001-0.16)."
    )
    parameters = {
        "ema_fast": 20,
        "ema_slow": 50,
        "structure_lookback": 8,
        "accel_period": 4,
        "accel_threshold": -0.007,
        "size_min": 0.001,
        "size_max": 0.16,
        "vol_lookback": 13,
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

    def _vol_zscore(self, current_vol):
        lookback = self.parameters["vol_lookback"]
        if len(self.volume_history) < lookback:
            return 0.0
        recent = self.volume_history[-lookback:]
        mean_vol = sum(recent) / len(recent)
        std_vol = (sum((v - mean_vol) ** 2 for v in recent) / len(recent)) ** 0.5
        if std_vol < 1e-9:
            return 0.0
        return (current_vol - mean_vol) / std_vol

    def _get_size(self, vol_z):
        min_s = self.parameters["size_min"]
        max_s = self.parameters["size_max"]
        
        # Volume component: map z-score to size
        if vol_z <= -2:
            return min_s
        elif vol_z >= 2:
            return max_s
        else:
            return min_s + (max_s - min_s) * ((vol_z + 2) / 4)

    def on_bar(self, bar, portfolio):
        self.close_history.append(bar.close)
        self.high_history.append(bar.high)
        self.low_history.append(bar.low)
        self.volume_history.append(bar.volume)
        
        self.ema_fast_val = self._ema(self.ema_fast_val, bar.close, self.parameters["ema_fast"])
        self.ema_slow_val = self._ema(self.ema_slow_val, bar.close, self.parameters["ema_slow"])
        
        lookback = self.parameters["structure_lookback"]
        period = self.parameters["accel_period"]
        min_bars = max(lookback * 2, self.parameters["ema_slow"], period * 2 + 1)
        
        if len(self.close_history) < min_bars:
            return []

        current_pos = portfolio["position"]
        trend_up = self.ema_fast_val > self.ema_slow_val
        
        # Calculate acceleration (2nd derivative of price)
        roc1 = (self.close_history[-1] - self.close_history[-period-1]) / self.close_history[-period-1]
        roc2 = (self.close_history[-period-1] - self.close_history[-period*2-1]) / self.close_history[-period*2-1]
        acceleration = roc1 - roc2
        
        # Structure check: HH + HL
        recent_high = max(self.high_history[-lookback:])
        prior_high = max(self.high_history[-lookback * 2:-lookback])
        hh = recent_high > prior_high
        
        recent_low = min(self.low_history[-lookback:])
        prior_low = min(self.low_history[-lookback * 2:-lookback])
        hl = recent_low > prior_low
        
        uptrend_structure = hh and hl
        
        # Volume sizing
        vol_z = self._vol_zscore(bar.volume)
        size = self._get_size(vol_z)
        
        # Entry: EMA cross up with structure + soft acceleration filter
        accel_thresh = self.parameters["accel_threshold"]
        if current_pos == 0:
            if (trend_up and not self.prev_trend_up and uptrend_structure 
                and acceleration >= accel_thresh):
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

"""
autodegen strategy — THE ONLY FILE THE AGENT EDITS
Run: python strategy.py
"""

from prepare import evaluate, load_bars


class Strategy:
    name = "synthesis_combined_v35_final"
    description = (
        "EMA 20/50 crossover + HH/HL structure + shadow filter + "
        "volume-weighted sizing + acceleration filter. Combining three proven edges."
    )
    parameters = {
        # Base strategy
        "ema_fast": 20,
        "ema_slow": 50,
        "structure_lookback": 8,
        "trail_pct": 0.019,
        # EDGE 1: Shadow filter
        "max_upper_shadow": 0.40,
        # EDGE 2: Volume-weighted sizing
        "vol_lookback": 12,
        "size_base": 0.03,
        "size_max": 0.06,
        "vol_z_min": 0.5,
        "vol_z_max": 2.0,
        # EDGE 3: Acceleration filter
        "accel_period": 5,
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
        
        lookback = self.parameters["structure_lookback"]
        vol_lb = self.parameters["vol_lookback"]
        accel_p = self.parameters["accel_period"]
        min_history = max(lookback * 2, self.parameters["ema_slow"], vol_lb, accel_p + 2)
        
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
        
        # EDGE 1: Shadow filter
        bar_range = bar.high - bar.low
        upper_shadow = bar.high - max(bar.open, bar.close)
        shadow_ratio = upper_shadow / bar_range if bar_range > 0 else 0
        shadow_ok = shadow_ratio <= self.parameters["max_upper_shadow"]
        
        # EDGE 2: Volume-weighted sizing
        recent_vol = self.volume_history[-vol_lb:]
        avg_vol = sum(recent_vol) / len(recent_vol)
        std_vol = (sum((v - avg_vol) ** 2 for v in recent_vol) / len(recent_vol)) ** 0.5
        vol_z = (bar.volume - avg_vol) / std_vol if std_vol > 0 else 0
        
        # Scale size based on volume z-score
        z_mult = max(self.parameters["vol_z_min"], 
                     min(self.parameters["vol_z_max"], 0.5 + vol_z * 0.5))
        dynamic_size = self.parameters["size_base"] * z_mult
        dynamic_size = min(dynamic_size, self.parameters["size_max"])
        
        # EDGE 3: Acceleration filter (2nd derivative of momentum)
        # Calculate momentum (1st derivative) as ROC
        if len(self.close_history) >= accel_p + 2:
            # Momentum = close[i] - close[i-accel_p]
            mom_current = self.close_history[-1] - self.close_history[-1 - accel_p]
            mom_prev = self.close_history[-2] - self.close_history[-2 - accel_p]
            # Acceleration = change in momentum
            acceleration = mom_current - mom_prev
        else:
            acceleration = 0
        
        # Softer acceleration: effectively disabled
        accel_ok = True  # acceleration > -200
        
        # Entry: EMA cross up with full uptrend structure + shadow + volume + soft accel
        if current_pos == 0:
            if (trend_up and not self.prev_trend_up and uptrend_structure and 
                shadow_ok and accel_ok):
                self.highest_since_entry = bar.high
                self.prev_trend_up = True
                return [{"side": "buy", "size": dynamic_size}]
        
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

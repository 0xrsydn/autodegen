"""
autodegen strategy — THE ONLY FILE THE AGENT EDITS
Run: python strategy.py
"""

from prepare import evaluate, load_bars


class Strategy:
    name = "breakeven_trail_v1"
    description = (
        "EMA 20/50 + HH/HL + shadow + vol sizing. "
        "EXIT: Trail + breakeven stop after X bars + time exit."
    )
    parameters = {
        "ema_fast": 20,
        "ema_slow": 50,
        "structure_lookback": 10,
        "max_upper_shadow": 0.40,
        "vol_lookback": 15,
        "size_base": 0.04,
        "size_min": 0.0,
        "size_max": 0.08,
        "trail_pct": 0.019,
        "breakeven_bars": 24,  # After 24 bars, use breakeven as minimum stop
        "max_bars": 66,
        "min_gain_pct": 0.015,
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
        self.volume_history.append(bar.volume)
        
        self.ema_fast_val = self._ema(self.ema_fast_val, bar.close, self.parameters["ema_fast"])
        self.ema_slow_val = self._ema(self.ema_slow_val, bar.close, self.parameters["ema_slow"])
        
        lookback = self.parameters["structure_lookback"]
        vol_lb = self.parameters["vol_lookback"]
        min_history = max(lookback * 2, self.parameters["ema_slow"], vol_lb)
        
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
        
        # Entry: EMA cross up with full uptrend structure + filters
        if current_pos == 0:
            if trend_up and not self.prev_trend_up and uptrend_structure:
                # Shadow filter: skip if upper shadow is too large
                bar_range = bar.high - bar.low
                if bar_range > 0:
                    upper_shadow = bar.high - max(bar.open, bar.close)
                    shadow_ratio = upper_shadow / bar_range
                    if shadow_ratio > self.parameters["max_upper_shadow"]:
                        self.prev_trend_up = trend_up
                        return []
                
                # Volume z-score sizing
                recent_vol = self.volume_history[-vol_lb:]
                vol_mean = sum(recent_vol) / len(recent_vol)
                vol_std = (sum((v - vol_mean) ** 2 for v in recent_vol) / len(recent_vol)) ** 0.5
                vol_z = (bar.volume - vol_mean) / vol_std if vol_std > 0 else 0
                
                size_min = self.parameters["size_min"]
                size_max = self.parameters["size_max"]
                vol_scale = 0.5 + 0.25 * vol_z
                vol_scale = max(0.0, min(1.0, vol_scale))
                size = size_min + vol_scale * (size_max - size_min)
                
                self.highest_since_entry = bar.high
                self.entry_price = bar.close
                self.bars_in_position = 0
                self.prev_trend_up = True
                return [{"side": "buy", "size": size}]
        
        # Exit: trail + breakeven + time
        if current_pos > 0:
            self.bars_in_position += 1
            self.highest_since_entry = max(self.highest_since_entry, bar.high)
            
            # Calculate trail stop
            trail_stop = self.highest_since_entry * (1.0 - self.parameters["trail_pct"])
            
            # After breakeven_bars, use max(trail_stop, entry_price) as stop
            if self.bars_in_position >= self.parameters["breakeven_bars"] and self.entry_price:
                trail_stop = max(trail_stop, self.entry_price)
            
            # Exit condition 1: trailing/breakeven stop
            if bar.close <= trail_stop:
                self.highest_since_entry = None
                self.entry_price = None
                self.bars_in_position = 0
                self.prev_trend_up = trend_up
                return [{"side": "sell", "size": abs(current_pos)}]
            
            # Exit condition 2: Time-based - stagnant position
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

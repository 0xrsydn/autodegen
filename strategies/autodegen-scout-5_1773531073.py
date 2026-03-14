"""
autodegen strategy — THE ONLY FILE THE AGENT EDITS
Run: python strategy.py
"""

from prepare import evaluate, load_bars


class Strategy:
    name = "time_exit_102_lb10_v1"
    description = (
        "EMA 20/50 with time-based exit (102 bars) and lookback 10. "
        "Optimal time exit for trend capture."
    )
    parameters = {
        "ema_fast": 20,
        "ema_slow": 50,
        "structure_lookback": 10,
        "max_bars": 102,
        "size": 0.04,
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
        self.bars_in_trade = 0

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
        
        # Entry: EMA cross up with structure
        if current_pos == 0:
            if trend_up and not self.prev_trend_up and uptrend_structure:
                self.highest_since_entry = bar.high
                self.bars_in_trade = 0
                self.prev_trend_up = True
                return [{"side": "buy", "size": self.parameters["size"]}]
        
        # Exit: trailing stop OR time-based exit
        if current_pos > 0:
            self.bars_in_trade += 1
            self.highest_since_entry = max(self.highest_since_entry, bar.high)
            
            # Time-based exit
            time_exit = self.bars_in_trade >= self.parameters["max_bars"]
            
            # Trailing stop
            trail_stop = self.highest_since_entry * (1.0 - self.parameters["trail_pct"])
            trail_exit = bar.close <= trail_stop
            
            if time_exit or trail_exit:
                self.highest_since_entry = None
                self.bars_in_trade = 0
                self.prev_trend_up = trend_up
                return [{"side": "sell", "size": abs(current_pos)}]
        
        self.prev_trend_up = trend_up
        return []


# ---- DO NOT EDIT BELOW THIS LINE ----
if __name__ == "__main__":
    bars = load_bars()
    evaluate(Strategy, bars)

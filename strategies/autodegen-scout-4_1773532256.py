"""
autodegen strategy — THE ONLY FILE THE AGENT EDITS
Run: python strategy.py

Multi-timeframe filter on shadow + vol sizing base.
Base: EMA 20/50 + HH/HL structure + vol sizing
Filter: Price above daily SMA (30-bar)
"""

from prepare import evaluate, load_bars


class Strategy:
    name = "multi_tf_daily_sma_v2"
    description = (
        "Shadow + vol sizing with daily SMA filter. "
        "Only enter when price > 30-bar SMA."
    )
    parameters = {
        # 1h EMA parameters
        "ema_fast": 20,
        "ema_slow": 50,
        # Daily SMA
        "daily_sma_period": 30,
        # Structure
        "structure_lookback": 8,
        # Vol sizing
        "vol_lookback": 15,
        "base_size": 0.04,
        "vol_size_mult": 0.9,
        # Exit
        "trail_pct": 0.018,
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

    def _sma(self, history, period):
        if len(history) < period:
            return None
        return sum(history[-period:]) / period

    def on_bar(self, bar, portfolio):
        self.close_history.append(bar.close)
        self.high_history.append(bar.high)
        self.low_history.append(bar.low)
        
        # 1h EMAs
        self.ema_fast_val = self._ema(self.ema_fast_val, bar.close, self.parameters["ema_fast"])
        self.ema_slow_val = self._ema(self.ema_slow_val, bar.close, self.parameters["ema_slow"])
        
        lookback = self.parameters["structure_lookback"]
        daily_period = self.parameters["daily_sma_period"]
        min_bars = max(lookback * 2, self.parameters["ema_slow"], daily_period)
        if len(self.close_history) < min_bars:
            return []

        current_pos = portfolio["position"]
        
        # 1h trend
        trend_1h = self.ema_fast_val > self.ema_slow_val
        
        # Daily SMA filter
        daily_sma = self._sma(self.close_history, daily_period)
        above_daily_sma = bar.close > daily_sma
        
        # Structure check: HH + HL (1h)
        recent_high = max(self.high_history[-lookback:])
        prior_high = max(self.high_history[-lookback * 2:-lookback])
        hh = recent_high > prior_high
        
        recent_low = min(self.low_history[-lookback:])
        prior_low = min(self.low_history[-lookback * 2:-lookback])
        hl = recent_low > prior_low
        
        uptrend_structure = hh and hl
        
        # Entry: 1h cross up + daily SMA + 1h structure
        if current_pos == 0:
            cross_up = trend_1h and not self.prev_trend_up
            multi_tf_confirm = above_daily_sma
            
            if cross_up and multi_tf_confirm and uptrend_structure:
                # Volatility-based sizing
                closes = self.close_history[-self.parameters["vol_lookback"]:]
                returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
                vol = (sum(r**2 for r in returns) / len(returns)) ** 0.5 if returns else 0.01
                
                # Inverse vol sizing
                size = self.parameters["base_size"]
                if vol > 0:
                    size = min(0.05, self.parameters["base_size"] * self.parameters["vol_size_mult"] / (vol * 100))
                
                self.highest_since_entry = bar.high
                self.prev_trend_up = True
                return [{"side": "buy", "size": size}]
        
        # Exit: trailing stop
        if current_pos > 0:
            self.highest_since_entry = max(self.highest_since_entry, bar.high)
            trail_stop = self.highest_since_entry * (1.0 - self.parameters["trail_pct"])
            if bar.close <= trail_stop:
                self.highest_since_entry = None
                self.prev_trend_up = trend_1h
                return [{"side": "sell", "size": abs(current_pos)}]
        
        self.prev_trend_up = trend_1h
        return []


# ---- DO NOT EDIT BELOW THIS LINE ----
if __name__ == "__main__":
    bars = load_bars()
    evaluate(Strategy, bars)

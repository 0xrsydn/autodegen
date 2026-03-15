"""
autodegen strategy — Trend + Volume + HH/HL v2
Lower volume threshold for more trades
"""

from prepare import evaluate, load_bars


class Strategy:
    name = "trend_vol_hhhl_v2"
    description = "EMA 20/50 + vol > 0.9× mean + HH/HL(10)"
    parameters = {
        "ema_fast": 20,
        "ema_slow": 50,
        "vol_lb": 20,
        "vol_mult": 0.9,
        "struct_lb": 10,
        "size": 0.04,
        "trail_pct": 0.022,
    }

    def initialize(self, train_data):
        self.close_history = []
        self.high_history = []
        self.low_history = []
        self.vol_history = []
        self.ema_fast = None
        self.ema_slow = None
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
        self.vol_history.append(bar.volume)
        
        ema_fast = self.parameters["ema_fast"]
        ema_slow = self.parameters["ema_slow"]
        vol_lb = self.parameters["vol_lb"]
        vol_mult = self.parameters["vol_mult"]
        struct_lb = self.parameters["struct_lb"]
        
        min_history = max(ema_slow, struct_lb * 2, vol_lb)
        if len(self.close_history) < min_history:
            return []

        current_pos = portfolio["position"]
        
        # EMAs
        self.ema_fast = self._ema(self.ema_fast, bar.close, ema_fast)
        self.ema_slow = self._ema(self.ema_slow, bar.close, ema_slow)
        trend_up = self.ema_fast > self.ema_slow
        
        # Volume confirmation
        recent_vol = self.vol_history[-vol_lb:]
        vol_mean = sum(recent_vol) / len(recent_vol)
        vol_confirmed = bar.volume > vol_mult * vol_mean
        
        # HH/HL Structure
        recent_high = max(self.high_history[-struct_lb:])
        prior_high = max(self.high_history[-struct_lb * 2:-struct_lb])
        hh = recent_high > prior_high
        
        recent_low = min(self.low_history[-struct_lb:])
        prior_low = min(self.low_history[-struct_lb * 2:-struct_lb])
        hl = recent_low > prior_low
        
        structure_confirmed = hh and hl
        
        # Entry
        if current_pos == 0:
            if trend_up and not self.prev_trend_up and structure_confirmed and vol_confirmed:
                self.highest_since_entry = bar.high
                self.prev_trend_up = True
                return [{"side": "buy", "size": self.parameters["size"]}]
        
        # Exit
        if current_pos > 0:
            self.highest_since_entry = max(self.highest_since_entry, bar.high)
            trail_stop = self.highest_since_entry * (1.0 - self.parameters["trail_pct"])
            if bar.close <= trail_stop:
                self.highest_since_entry = None
                self.prev_trend_up = trend_up
                return [{"side": "sell", "size": abs(current_pos)}]
        
        self.prev_trend_up = trend_up
        return []


if __name__ == "__main__":
    bars = load_bars()
    evaluate(Strategy, bars)

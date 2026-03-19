"""
autodegen strategy — THE ONLY FILE THE AGENT EDITS
Run: python strategy.py
"""

from prepare import evaluate, load_bars


class Strategy:
    name = "baseline_ema_cross_trail_v1"
    description = (
        "Simple baseline for 15m: EMA crossover + HH/HL structure + trailing stop. "
        "Starting point for iteration — expect FAIL on first run."
    )
    parameters = {
        "ema_fast": 20,
        "ema_slow": 50,
        "structure_lookback": 10,
        "trail_pct": 0.02,
        "size": 0.05,
    }

    def initialize(self, bars):
        self.closes = []
        self.ema_fast_val = None
        self.ema_slow_val = None
        self.prev_trend_up = False
        self.entry_price = None
        self.highest_since_entry = None

    def on_bar(self, bar, context):
        close = bar.close
        position = context["position"]
        self.closes.append(close)
        p = self.parameters

        # EMA update
        fast_k = 2.0 / (p["ema_fast"] + 1)
        slow_k = 2.0 / (p["ema_slow"] + 1)
        if self.ema_fast_val is None:
            self.ema_fast_val = close
            self.ema_slow_val = close
        else:
            self.ema_fast_val = close * fast_k + self.ema_fast_val * (1 - fast_k)
            self.ema_slow_val = close * slow_k + self.ema_slow_val * (1 - slow_k)

        # Need enough history
        lb = p["structure_lookback"]
        if len(self.closes) < max(p["ema_slow"], lb) + 1:
            return []

        # Trend: EMA fast > slow
        trend_up = self.ema_fast_val > self.ema_slow_val

        # Structure: higher highs + higher lows over lookback
        recent = self.closes[-lb:]
        highs_rising = max(recent[-lb // 2 :]) > max(recent[: lb // 2])
        lows_rising = min(recent[-lb // 2 :]) > min(recent[: lb // 2])
        structure_bullish = highs_rising and lows_rising

        # Entry: trend crosses up + structure confirms
        if position == 0:
            if trend_up and not self.prev_trend_up and structure_bullish:
                self.entry_price = close
                self.highest_since_entry = close
                self.prev_trend_up = trend_up
                return [{"side": "buy", "size": p["size"]}]
        else:
            # Track highest for trailing stop
            if self.highest_since_entry is None:
                self.highest_since_entry = close
            self.highest_since_entry = max(self.highest_since_entry, close)

            trail_stop = self.highest_since_entry * (1.0 - p["trail_pct"])
            if close <= trail_stop:
                self.highest_since_entry = None
                self.entry_price = None
                self.prev_trend_up = trend_up
                return [{"side": "sell", "size": abs(position)}]

        self.prev_trend_up = trend_up
        return []


# ---- DO NOT EDIT BELOW THIS LINE ----
TIMEFRAME = "15m"

if __name__ == "__main__":
    bars = load_bars(timeframe=TIMEFRAME)
    evaluate(Strategy, bars, timeframe=TIMEFRAME)

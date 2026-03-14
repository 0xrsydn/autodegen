"""
autodegen strategy — THE ONLY FILE THE AGENT EDITS
Run: python strategy.py
"""

from prepare import evaluate, load_bars
import math


class Strategy:
    name = "entropy_pullback_v1"
    description = (
        "Enter when entropy is low AND pullback structure (LH+HL in uptrend). "
        "Buys pullbacks in predictable regimes."
    )
    parameters = {
        "entropy_window": 48,
        "n_bins": 10,
        "entropy_threshold": 0.95,
        "ema_fast": 20,
        "ema_slow": 50,
        "structure_lookback": 8,
        "size": 0.04,
        "trail_pct": 0.019,
    }

    def initialize(self, train_data):
        self.close_history = []
        self.high_history = []
        self.low_history = []
        self.ema_fast_val = None
        self.ema_slow_val = None
        self.highest_since_entry = None

    def _ema(self, prev, price, period):
        if prev is None:
            return price
        alpha = 2.0 / (period + 1)
        return alpha * price + (1.0 - alpha) * prev

    def _compute_entropy(self, returns, n_bins):
        if len(returns) < 10:
            return 1.0
        min_r, max_r = min(returns), max(returns)
        if max_r - min_r < 1e-12:
            return 0.0
        bin_width = (max_r - min_r) / n_bins
        counts = [0] * n_bins
        for r in returns:
            idx = int((r - min_r) / bin_width)
            idx = min(idx, n_bins - 1)
            counts[idx] += 1
        total = len(returns)
        entropy = 0.0
        for c in counts:
            if c > 0:
                p = c / total
                entropy -= p * math.log2(p)
        max_entropy = math.log2(n_bins)
        return entropy / max_entropy if max_entropy > 0 else 0.0

    def on_bar(self, bar, portfolio):
        self.close_history.append(bar.close)
        self.high_history.append(bar.high)
        self.low_history.append(bar.low)
        
        self.ema_fast_val = self._ema(self.ema_fast_val, bar.close, self.parameters["ema_fast"])
        self.ema_slow_val = self._ema(self.ema_slow_val, bar.close, self.parameters["ema_slow"])
        
        lookback = self.parameters["structure_lookback"]
        min_needed = max(lookback * 2, self.parameters["ema_slow"], self.parameters["entropy_window"] + 1)
        if len(self.close_history) < min_needed:
            return []

        # Compute entropy
        window = self.parameters["entropy_window"]
        closes = self.close_history[-(window+1):]
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        entropy = self._compute_entropy(returns, self.parameters["n_bins"])
        
        current_pos = portfolio["position"]
        trend_up = self.ema_fast_val > self.ema_slow_val
        
        # Structure check: LH + HL (pullback in uptrend)
        # Recent lower high and higher low = pullback
        recent_high = max(self.high_history[-lookback:])
        prior_high = max(self.high_history[-lookback * 2:-lookback])
        lh = recent_high < prior_high  # Lower high (pullback)
        
        recent_low = min(self.low_history[-lookback:])
        prior_low = min(self.low_history[-lookback * 2:-lookback])
        hl = recent_low > prior_low  # Higher low (uptrend structure)
        
        pullback_structure = lh and hl  # Pullback within uptrend

        # Entry: low entropy + trend up + pullback structure
        if current_pos == 0:
            if (entropy < self.parameters["entropy_threshold"] and 
                trend_up and 
                pullback_structure):
                self.highest_since_entry = bar.high
                return [{"side": "buy", "size": self.parameters["size"]}]
        
        # Exit: trailing stop
        if current_pos > 0:
            self.highest_since_entry = max(self.highest_since_entry, bar.high)
            trail_stop = self.highest_since_entry * (1.0 - self.parameters["trail_pct"])
            if bar.close <= trail_stop:
                self.highest_since_entry = None
                return [{"side": "sell", "size": abs(current_pos)}]
        
        return []


# ---- DO NOT EDIT BELOW THIS LINE ----
if __name__ == "__main__":
    bars = load_bars()
    evaluate(Strategy, bars)

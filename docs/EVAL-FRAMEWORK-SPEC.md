# autodegen evaluation framework spec
## GPT 5.4 Senior Quant Review — 2026-03-13

### Bottom line
Current setup is not reliable enough for agentic strategy search. Biggest issues:
- Far too little history (7 months = one mood swing)
- Trade-count gate meaningless
- No explicit overfit detection
- No complexity/trial-count penalty
- Single-metric optimization invites gaming

---

## 1) Data Requirements

### Use Binance BTCUSDT perp as primary history (3-6 years of 1h bars)
- Hyperliquid only has ~7 months — fatal for strategy search
- Regime structure matters more than venue purity on 1h+ bars
- Hyperliquid becomes venue-specific holdout validation, not training data
- Rule: if it can't survive on long Binance history, don't bother with Hyperliquid

### Minimum: 3 years of 1h data
Need exposure to: bull expansion, bear selloff, sideways chop, vol compression/expansion, liquidation cascades

### Timeframe: keep 1h execution, allow derived 4h/1d features
No sub-hour data yet. Higher-timeframe context (4h trend, daily vol regime) is high ROI.

### Assets: BTC only now, ETH as cross-asset validation later
Cross-asset validation is one of the strongest anti-bullshit checks.

### Regime labels (simple, no HMM needed)
- **Trend**: bull (30d return > +15%), bear (< -15%), sideways (otherwise)
- **Vol**: high (14d realized vol > long-run median), low (otherwise)
- Report metrics by regime bucket

---

## 2) Evaluation Metrics

### Core per-backtest metrics (compute for each fold + validation)

1. **Net return**: R = E_T/E_0 - 1
2. **CAGR**: (E_T/E_0)^(1/Y) - 1
3. **Max drawdown**: max_t(1 - E_t / max_{s<=t} E_s)
4. **Bar-return Sharpe** (PRIMARY — replaces trade-return Sharpe):
   - r_t = E_t/E_{t-1} - 1
   - Sharpe = mean(r)/std(r) * sqrt(8760)  [8760 = hours/year]
5. **Sortino**: mean(r)/std(r⁻) * sqrt(8760)
6. **Calmar**: CAGR / max(MDD, ε)
7. **Trade count**: closed trades
8. **Win rate**: #(pnl > 0) / n_trades
9. **Profit factor**: sum(pnl⁺) / |sum(pnl⁻)|
10. **Avg trade return**: mean(pnl_i / entry_value_i)
11. **Exposure**: fraction of bars with nonzero position
12. **Turnover**: sum(|Δposition| * price) / avg_equity
13. **Fee load**: total_fees / max(|gross_pnl|, ε)
14. **Worst bar loss**: min(r_t) — liquidation risk proxy

### Keep trade-return Sharpe as secondary diagnostic only
Bar-return Sharpe is primary — it captures mark-to-market risk that trade-return hides.

### Overfitting comparison metrics (ESSENTIAL)

A. **WF-to-holdout decay**: S_val / max(S_wf, ε) — near 1.0 = stable, <<1.0 = overfit
B. **Train-test gap per fold**: mean(S_train - S_test) — if train >> test, it's memorizing
C. **Fold variance**: std(S_test across folds), CV = std/|mean|
D. **Negative-fold ratio**: #(S_i < 0) / k — cleanest robustness check

---

## 3) Recommended Gates / Thresholds

### Minimum viability (ALL must pass)

**Data sufficiency:**
- Total history >= 3 years
- Validation segment >= 90 days
- Each test fold >= 30 days
- Each train fold >= 180 days

**Trading activity:**
- Total WF closed trades >= 30
- Avg closed trades per fold >= 5
- Validation closed trades >= 5

**Risk-adjusted performance:**
- Mean WF bar-Sharpe >= 0.75
- Holdout bar-Sharpe >= 0.25
- Mean WF Calmar >= 0.5
- Mean WF profit factor >= 1.10

**Risk containment:**
- Mean WF maxdd <= 25%
- Holdout maxdd <= 30%
- Worst fold maxdd <= 35%

**Robustness:**
- Worst fold Sharpe >= -0.5
- Negative-fold ratio <= 0.30
- Fold Sharpe std <= 1.25
- Train-test gap <= 0.75 Sharpe
- Holdout decay >= 0.50

**Friction:**
- Fee load <= 0.50

### Strong keep gate (for autonomous loop keep/revert)
- Mean WF Sharpe >= 1.0
- Holdout Sharpe >= 0.5
- Mean WF MDD <= 20%
- Negative-fold ratio <= 0.20
- Holdout decay >= 0.67
- Total WF trades >= 50

---

## 4) Walk-Forward Design

### Current problem
8-fold expanding over 7 months = some folds comically underpowered. Fake rigor.

### Recommended (with 3y+ history)
- **6 folds** (fewer, more meaningful)
- Each test fold: **45 days**
- Minimum train before first test: **180 days**
- Expanding train window
- Example: fold 1 = train 180d/test 45d, fold 2 = train 225d/test 45d, etc.

### With 5y+ history
- 8 folds, train min 270d, test 60d

### Train metrics
For each fold: run backtest on BOTH train and test data. Cheap and gives immediate overfit diagnostics.

---

## 5) Overfitting Safeguards

### Complexity penalty
- Track: n_params, AST node count
- Penalty: 0.02 * max(0, n_params - 3) + 0.001 * max(0, ast_nodes - 120)
- Hard gates: parameters <= 12, AST nodes <= 400

### Multiple testing correction (practical)
- Track experiment count N from results.tsv
- Penalty: 0.05 * log2(N + 1)
- Subtract from composite score
- Later: Deflated Sharpe Ratio, White's Reality Check

### Permutation test (implement soon, not now)
- Shuffle signal timing 100x, run same backtest
- Real strategy should be top 5th percentile
- Expensive but definitive

---

## 6) Composite Score

### Formula
```
composite = (
    0.40 * norm_sharpe      # bar-return Sharpe, capped [0, 3]
  + 0.15 * norm_sortino     # Sortino, capped [0, 5]
  + 0.15 * norm_calmar      # Calmar, capped [0, 3]
  + 0.10 * norm_pf          # profit factor, capped [1, 3]
  + 0.10 * stability        # 1 - negative_fold_ratio
  + 0.10 * decay_health     # min(holdout_decay, 1.0)
  - complexity_penalty
  - search_penalty
)
```

Normalize each sub-metric to [0, 1] using the cap ranges. Agent optimizes this single number.

---

## 7) Results.tsv Schema

```
timestamp | name | composite | bar_sharpe_wf | bar_sharpe_val | decay | maxdd_wf | maxdd_val | trades_wf | trades_val | fold_std | neg_folds | profit_factor | calmar | n_params | ast_nodes | status
```

Status: KEPT / REVERTED / CRASH

---

## 8) Implementation Priority

### Phase 1 (implement now — highest ROI)
1. Switch to Binance BTCUSDT perp data (3y+)
2. Bar-return Sharpe as primary metric
3. Train vs test metrics per fold
4. Holdout decay gate
5. Fold variance + negative-fold ratio gates
6. Raise trade count minimums
7. Composite score formula
8. Richer results.tsv schema

### Phase 2 (implement soon)
1. Regime labeling + per-regime reporting
2. Complexity penalty (AST + param count)
3. Search penalty (experiment count)
4. Sortino, Calmar, profit factor metrics
5. Cross-asset validation (ETH)

### Phase 3 (later)
1. Permutation test
2. Deflated Sharpe Ratio
3. Multi-venue validation
4. Funding rate integration

# STRESS TEST REPORT

**Generated:** 2026-03-15
**Tester:** stress-test-1 (adversary role)
**Dataset:** BTC/USDT:USDT 1h (2020-01-01 to 2026-03-14, 54348 bars)

---

## Executive Summary

| Strategy | Composite | Hard Gates | Verdict | Key Finding |
|----------|-----------|------------|---------|-------------|
| time_exit_66_g155_v1 | 0.771 | FAIL | **FRAGILE** | Time exit HURTS performance (+9.7% when removed) |
| shadow_vol_synth_lb10_vol15_sz0-8 | 0.846 | PASS | **FRAGILE** | Best baseline, but 4 fragile params |
| accel_filter_v1 | 0.605 | FAIL | **FRAGILE** | Accel filter is decorative; only HH/HL matters |

**Overall Assessment:** All three strategies are **FRAGILE** due to overfitting on EMA periods and trailing stop values. The HH/HL structure filter is the only consistently meaningful component.

---

## Strategy 1: time_exit_66_g155_v1

### Baseline
- **Composite:** 0.771
- **Hard Gates:** FAIL (fold_std=1.34 > 1.25)
- **Parameters:** 11

### Test Results

#### Parameter Robustness: FRAGILE
| Parameter | Change | Composite | Δ% | Status |
|-----------|--------|-----------|-----|--------|
| trail_pct | -20% (0.0152) | 0.568 | -26.4% | **FRAGILE** |
| trail_pct | +20% (0.0228) | 0.750 | -2.7% | robust |
| ema_fast | -20% (16) | 0.483 | -37.3% | **FRAGILE** |
| ema_slow | -20% (40) | N/A | N/A | **FRAGILE** (catastrophic) |
| ema_slow | +20% (60) | 0.490 | -36.5% | **FRAGILE** |
| structure_lookback | ±20% | 0.68-0.72 | -7% to -11% | robust |
| max_upper_shadow | ±20% | 0.74-0.76 | -1% to -4% | robust |
| vol_lookback | ±20% | 0.74-0.76 | -1% to -4% | robust |
| time_exit_bars | +20% (79) | 0.856 | +11.0% | robust (better!) |
| gain_threshold | ±20% | 0.77-0.79 | ±2% | robust |

**Fragile Parameters:** 4 (trail_pct, ema_fast, ema_slow x2)

#### Component Ablation: PASS (all meaningful)
| Component Removed | Composite | Δ% | Status |
|-------------------|-----------|-----|--------|
| Shadow filter | 0.630 | -18.3% | meaningful |
| HH/HL structure | 0.681 | -11.6% | meaningful |
| Vol sizing | 0.682 | -11.6% | meaningful |
| **Time exit** | **0.846** | **+9.7%** | **COUNTERPRODUCTIVE** |

**Key Finding:** The time exit component actually HURTS performance. Removing it improves composite by 9.7%.

#### Complexity: BLOATED
- Parameters: 11
- Lines: 138
- **Recommendation:** Remove time_exit_bars and gain_threshold to get shadow_vol_synth strategy (9 params)

#### Leverage Audit: PASS
- At size_max=0.05: composite=0.768, Δ=-0.4% (edge-only)
- At size_max=0.04 (fixed): composite=0.682, Δ=-11.6% (some sizing sensitivity)

### VERDICT: FRAGILE
- Time exit is counterproductive (strategy improves without it)
- EMA parameters are extremely fragile
- Baseline fails hard gates due to fold_std

---

## Strategy 2: shadow_vol_synth_lb10_vol15_sz0-8

### Baseline
- **Composite:** 0.846
- **Hard Gates:** PASS
- **Parameters:** 9

### Test Results

#### Parameter Robustness: FRAGILE
| Parameter | Change | Composite | Δ% | Status |
|-----------|--------|-----------|-----|--------|
| trail_pct | -20% (0.0152) | 0.702 | -17.0% | **FRAGILE** |
| trail_pct | +20% (0.0228) | 0.867 | +2.5% | robust |
| ema_fast | -20% (16) | 0.594 | -29.7% | **FRAGILE** |
| ema_slow | -20% (40) | 0.415 | -50.9% | **FRAGILE** |
| ema_slow | +20% (60) | 0.580 | -31.4% | **FRAGILE** |
| structure_lookback | ±20% | 0.76-0.80 | -5% to -10% | robust |
| max_upper_shadow | ±20% | 0.80-0.82 | -3% to -5% | robust |
| vol_lookback | ±20% | 0.84 | -1% | robust |

**Fragile Parameters:** 4 (trail_pct, ema_fast, ema_slow x2)

#### Component Ablation: PASS (all meaningful)
| Component Removed | Composite | Δ% | Status |
|-------------------|-----------|-----|--------|
| Shadow filter | 0.738 | -12.7% | meaningful |
| HH/HL structure | 0.716 | -15.4% | meaningful |
| Vol sizing | 0.799 | -5.5% | meaningful |

**Key Finding:** All three components add real value. This is the "cleanest" strategy.

#### Complexity: ACCEPTABLE
- Parameters: 9
- Lines: 120
- All components are meaningful

#### Leverage Audit: PASS
- At size_max=0.05: composite=0.845, Δ=-0.2% (edge-only)
- At size_max=0.04 (fixed): composite=0.799, Δ=-5.5% (some sizing sensitivity)

### VERDICT: FRAGILE
- Best baseline composite (0.846) and passes hard gates
- However, EMA and trail_pct parameters are extremely fragile
- All components are meaningful (no decoration)

---

## Strategy 3: accel_filter_v1

### Baseline
- **Composite:** 0.605
- **Hard Gates:** FAIL
- **Parameters:** 8
- **Notable:** Lowest maxdd (0.003796) but only 30 WF trades

### Test Results

#### Parameter Robustness: FRAGILE
| Parameter | Change | Composite | Δ% | Status |
|-----------|--------|-----------|-----|--------|
| trail_pct | -20% (0.0152) | 0.451 | -25.5% | **FRAGILE** |
| ema_fast | -20% (16) | 0.450 | -25.6% | **FRAGILE** |
| ema_fast | +20% (24) | 0.733 | +21.3% | **FRAGILE** (better!) |
| ema_slow | +20% (60) | 0.507 | -16.1% | **FRAGILE** |
| accel_lookback | -33% (2) | 0.720 | +19.1% | **FRAGILE** (better!) |
| structure_lookback | ±20% | 0.54-0.60 | -11% to 0% | robust |

**Fragile Parameters:** 5 (trail_pct, ema_fast x2, ema_slow, accel_lookback)

**Key Finding:** The "fragile" changes that IMPROVE performance suggest the baseline is suboptimally tuned.

#### Component Ablation: DECORATIVE ACCEL
| Component Removed | Composite | Δ% | Status |
|-------------------|-----------|-----|--------|
| HH/HL structure | 0.512 | -15.4% | meaningful |
| Vol sizing | 0.605 | 0% | decorative |
| **Acceleration filter** | **0.597** | **-1.2%** | **DECORATIVE** |

**Key Finding:** The acceleration filter adds almost no value (-1.2%). HH/HL structure is the only meaningful component.

#### Complexity: OVERFIT
- Parameters: 8
- Lines: 103
- Acceleration filter is decorative
- Vol sizing is already fixed at 0.04 (no actual sizing)

#### Leverage Audit: PASS
- Strategy uses fixed sizing (0.04), no leverage exploit possible

### VERDICT: FRAGILE
- Acceleration filter is decorative (core claim of strategy is false)
- Only HH/HL structure is meaningful
- Baseline fails hard gates

---

## Cross-Strategy Analysis

### Common Fragilities
1. **trail_pct (0.019):** Extremely sensitive to decreases (-20% change causes 17-26% composite drop)
2. **EMA periods (20/50):** Both ema_fast and ema_slow are fragile across all strategies
3. **HH/HL structure:** The ONLY consistently meaningful component

### Common Robustness
1. **structure_lookback:** ±20% changes cause <15% composite drop
2. **shadow filter (max_upper_shadow):** Robust to ±20% changes
3. **vol_lookback:** Robust to ±20% changes

### Leverage Analysis
- None of the strategies are pure leverage exploits
- Sizing adds 5-12% to composite but is not the primary edge
- The edge comes from the entry signal (EMA cross + HH/HL structure)

---

## Recommendations

### 1. Simplify Strategy 1 → Strategy 2
The time_exit_66_g155_v1 strategy should be **abandoned** in favor of shadow_vol_synth_lb10_vol15_sz0-8:
- Time exit is counterproductive (+9.7% improvement when removed)
- Same core logic without the problematic time exit component

### 2. Hardcode EMA Periods
Since EMA periods are fragile but the optimal values (20/50) are well-established:
- Consider hardcoding ema_fast=20, ema_slow=50
- Remove these as tunable parameters to reduce overfitting risk

### 3. Add Robustness to trail_pct
The trailing stop at 1.9% is extremely sensitive. Consider:
- Testing a range of trailing stops (1.5% to 2.5%)
- Using ATR-based trailing stops instead of fixed percentage

### 4. Acceleration Filter is Dead
The accel_filter_v1 strategy's core innovation (acceleration filter) is decorative. Do not pursue this direction further.

---

## Final Ranking by Robustness

| Rank | Strategy | Composite | Hard Gates | Fragile Params | Meaningful Components | Verdict |
|------|----------|-----------|------------|----------------|----------------------|---------|
| 1 | shadow_vol_synth_lb10_vol15_sz0-8 | 0.846 | PASS | 4 | 3 (shadow, HH/HL, vol) | FRAGILE |
| 2 | time_exit_66_g155_v1 | 0.771 | FAIL | 4 | 4 (but time exit hurts) | FRAGILE |
| 3 | accel_filter_v1 | 0.605 | FAIL | 5 | 1 (HH/HL only) | FRAGILE |

**Winner:** shadow_vol_synth_lb10_vol15_sz0-8 is the best of the three, but still fragile due to EMA/trail_pct sensitivity.

---

## Methodology Notes

- **Parameter Perturbation:** ±20% change from baseline values
- **Fragility Threshold:** >15% composite change on ±20% parameter change
- **Component Ablation:** Remove one component at a time, check Δ%
- **Meaningful Threshold:** >5% composite change when removed
- **Leverage Audit:** Test at size_max=0.05 and size_max=0.04 (fixed)

---

*Report generated by stress-test-1 (adversary role). Honest verdicts, no sugarcoating.*

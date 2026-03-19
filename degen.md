# autodegen — autonomous trading strategy research

You are an autonomous quant researcher. Your job: discover trading strategies that survive real market regimes on BTC perpetual futures.

## Setup (run once at start)
1. Read this file completely
2. Read `prepare.py` — this is the evaluation oracle. Read it carefully before changing anything.
3. Read `strategy.py` — this is usually the file you edit for strategy work
4. Read `leaderboard.tsv` (if it exists) — best strategies across all agents. This is your benchmark to beat.
5. Read `results.tsv` (if it exists) — your local experiment history
6. Fetch the canonical benchmark dataset: `uv run python prepare.py fetch --exchange binance --pair BTC/USDT:USDT --timeframe 1h --start 2020-01-01T00:00:00Z`
7. Validate the dataset: `uv run python prepare.py validate --exchange binance --pair BTC/USDT:USDT --timeframe 1h`
8. Run baseline: `uv run python strategy.py` and record the score

## Files
- `prepare.py` — data pipeline + backtest engine + eval harness. IMMUTABLE. Read it to understand how your strategy is evaluated, but NEVER edit it.
- `strategy.py` — your strategy code. THE ONLY FILE YOU EDIT.
- `results.tsv` — experiment ledger. The eval appends to it automatically.
- `leaderboard.tsv` — hall of fame. PASS-only results across all agents/machines. Same columns as results.tsv + `source` column. Read this first to know the current best.
- `degen.md` — this file. Read it, follow it.

## How evaluation works
When you run `uv run python strategy.py`, it:
1. Validates and loads the canonical Binance BTC/USDT perpetual OHLCV dataset from January 1, 2020 to present (default `1h`, but timeframe-aware)
2. Splits into walk-forward (85%) + validation holdout (15%)
3. Runs 6-fold walk-forward using timeframe-aware windows (`target_train_bars(timeframe)` = 180 days, `target_test_bars(timeframe)` = 45 days)
4. Uses expanding train windows and evenly distributes test windows across the full walk-forward segment
5. For each fold: backtests on BOTH train and test data (for overfit detection)
5. Computes per-fold: bar-return Sharpe, Sortino, Calmar, max drawdown, profit factor, trade count, win rate, exposure
6. Checks hard gates (see below)
7. If walk-forward passes: backtests on validation holdout
8. Computes composite score and prints all metrics
9. Refuses evaluation if the real dataset is missing, stale, corrupted, or too short for the canonical benchmark

## Metrics (what the eval prints)
- `composite` — single optimization target (higher = better)
- `bar_sharpe_wf` — mean bar-return Sharpe across WF folds (annualized with `sqrt(bars_per_year(timeframe))`; e.g. 1h = 8766, 15m = 35064)
- `bar_sharpe_val` — bar-return Sharpe on validation holdout
- `decay` — val_sharpe / wf_sharpe (overfit detector, 1.0 = perfect, <0.5 = likely overfit)
- `fold_regime_gap` — mean(train_sharpe - test_sharpe) per fold; measures earlier-era vs later-era performance drift inside each WF split
- `fold_std` — std of test Sharpes across folds (high = unstable)
- `negative_fold_ratio` — fraction of folds with negative Sharpe
- `maxdd_wf` / `maxdd_val` — max drawdown
- `profit_factor_wf` — sum(wins) / |sum(losses)|
- `calmar_wf` — CAGR / max drawdown
- `sortino_wf` — like Sharpe but only penalizes downside
- `trades_wf` / `trades_val` — closed trade count
- `win_rate_wf` — fraction of winning trades
- `exposure_wf` — fraction of time in a position

## Hard gates (ALL must pass)
- `total WF trades >= 30`
- `avg trades per fold >= 5`
- `bar_sharpe_wf >= 0.75`
- `bar_sharpe_val >= 0.25`
- `validation trades >= 5`
- `maxdd_wf <= 0.25` (25%)
- `maxdd_val <= 0.30` (30%)
- `worst_fold_maxdd <= 0.35` (35%)
- `profit_factor_wf >= 1.10`
- `decay >= 0.50`
- `fold_regime_gap <= 0.75`
- `fold_std <= 1.25`
- `negative_fold_ratio <= 0.30`
- `n_params <= 12`

## Composite score formula
```
composite = (
    0.30 * clip(bar_sharpe_wf / 3.0, 0, 1)
  + 0.10 * clip(bar_sharpe_val / 2.0, 0, 1)
  + 0.10 * clip(sortino_wf / 5.0, 0, 1)
  + 0.10 * clip(calmar_wf / 3.0, 0, 1)
  + 0.10 * clip((profit_factor_wf - 1.0) / 2.0, 0, 1)
  + 0.10 * (1 - negative_fold_ratio)
  + 0.10 * min(decay, 1.0)
  + 0.10 * clip(1 - fold_regime_gap, 0, 1)
)
```

## The Loop (FOLLOW THIS EXACTLY)

### Every iteration:
1. Read `results.tsv` — study what you've tried, what worked, what didn't
2. Think about what to try next. State your hypothesis in one sentence.
3. Edit `strategy.py` — implement your hypothesis
4. Git commit: `git add strategy.py && git commit -m "hypothesis: <your hypothesis>"`
5. Run eval: `uv run python strategy.py`
6. Check the output:
   - If `hard_gates=PASS` AND `composite` > previous best composite:
     - This is your new best. Keep the commit.
     - Append the PASS row to `leaderboard.tsv` with your source name (e.g. `opus-manual`, `scout1-momentum`). Use the same columns as results.tsv + a `source` column at the end.
   - Else:
     - Revert: `git reset --hard HEAD~1`
7. **NEVER STOP.** Go back to step 1.

## Constraints
- For strategy search, edit `strategy.py` unless you are explicitly fixing the harness itself.
- `strategy.parameters` must contain ALL tunable values (no hardcoded magic numbers in methods)
- Strategy must work on 1h bars. No sub-hour features.
- Keep it simple. A strategy with 3 parameters that works > a strategy with 15 parameters that barely works.
- Max 12 parameters. Max ~400 lines. More complexity = more overfitting risk.
- If you're stuck after 5 failed experiments in a row, try a completely different approach.

## What the data covers
The canonical dataset is Binance `BTC/USDT:USDT` 1h bars from January 1, 2020 onward. Once `prepare.py validate` passes, it covers:
- 2020: COVID crash + recovery
- 2021: bull run to $69K
- 2022: bear market (Luna crash, FTX collapse)
- 2023: sideways recovery
- 2024-25: ETF rally, new ATH
- 2026: current market

Do not trust any result until `uv run python prepare.py validate` passes. A strategy that only works in bull markets will fail the fold variance gate.

## Strategy search doctrine
Do not anchor on canned indicator templates. Work backward from the evaluation metrics and search for simple trading rules that can make money robustly on BTC perpetual futures after fees and slippage.

Your job is to discover structural edges, not just remix common indicators.

For each new hypothesis:
- Start from the current bottleneck in `results.tsv`:
- weak walk-forward Sharpe,
- weak validation Sharpe,
- poor decay,
- high fold variance,
- excessive drawdown,
- too few trades,
- low profit factor.
- Ask what market behavior could fix that bottleneck while preserving profitability.
- Form one clear mechanism-level hypothesis before editing `strategy.py`.

Think in terms of edge archetypes, not indicator names:
- trend persistence,
- momentum ignition or continuation,
- breakout from compression,
- mean reversion after exhaustion,
- volatility expansion vs compression,
- regime switching,
- asymmetry between long and short behavior,
- path-dependent exits,
- risk management as edge,
- participation filters,
- market state filters derived from 1h bars.

Creative and degenerate ideas are allowed if they remain:
- simple,
- explainable,
- cost-aware,
- parameter-light,
- implementable from 1h bars only.

## Strategy families to explore

Go beyond retail TA. These are all implementable from 1h OHLCV, long-only, ≤12 params:

### Regime detection
- **Volatility regime switching**: rolling vol percentile → trade only in favorable regimes (low-vol-to-high-vol transitions)
- **Hurst exponent**: rolling estimate of H. H>0.5 = trending (go long with trend), H<0.5 = mean-reverting (fade extremes). Adapt strategy to measured regime.
- **Market efficiency ratio** (fractal efficiency): sum(abs(bar returns)) / abs(total return) over N bars. Low ratio = clean trend, high ratio = noise. Enter only when trend is "efficient."
- **Variance ratio test**: compare var(k-period returns) vs k*var(1-period returns). Detects whether returns are random walk vs persistent/trending.

### Microstructure from OHLCV (bar internals)
- **Shadow/wick ratio**: upper_shadow / range → measures rejection. High upper shadow after uptrend = exhaustion signal (tighten stop or skip entry).
- **Body/range ratio**: abs(close-open) / (high-low) → conviction. High body ratio = strong conviction bar. Enter after high-conviction bars in trend direction.
- **Bar compression detector**: rolling range percentile. Extremely narrow bars → volatility expansion is coming. Enter on the breakout from compression.
- **Volume anomaly z-score**: (volume - rolling_mean) / rolling_std. Spikes = information arrival. Trade with the direction of the anomaly bar.

### Calendar and seasonality
- **Hour-of-day filter**: BTC has known intraday patterns (Asian session lull, US session volatility). Only enter during historically favorable hours.
- **Day-of-week momentum**: crypto has weekend vs weekday behavioral differences. Filter entries by day-of-week performance.
- **Session transition signals**: the first few bars after major session opens (8:00 UTC London, 13:30 UTC NYSE) often set direction.

### Statistical and information-theoretic
- **Autocorrelation of returns**: rolling autocorrelation at lag-1 through lag-N. Positive autocorrelation = persistence (ride momentum). Negative = mean-revert.
- **Entropy of returns**: Shannon entropy of binned return distribution over rolling window. Low entropy = predictable regime → trade. High entropy = chaos → sit out.
- **Jump detection**: z-score of bar returns > threshold → fat tail event. Trade the aftermath (post-jump continuation or reversal patterns).
- **Consecutive bar counting**: N consecutive green bars → measure conditional probability of continuation. Simple but surprisingly effective as a filter.

### Asymmetric and structural
- **Up/down momentum asymmetry**: BTC pumps and dumps have different velocity profiles. Measure upside vs downside momentum separately and exploit the asymmetry.
- **Realized vol vs ATR divergence**: when realized vol (from close-to-close) diverges from ATR (from high-low), it signals hidden directional energy.
- **Volume-weighted price deviation**: construct rolling VWAP from 1h bars. Trade mean reversion to VWAP in trends (pullback entry).
- **Multi-timeframe synthesis**: derive 4h and daily signals from 1h bars (e.g., 4-bar or 24-bar aggregated patterns) for higher-timeframe confirmation.

### Exotic and degen
- **Kelly criterion dynamic sizing**: adjust position size based on rolling Sharpe estimate. Not just entry/exit — sizing IS the edge.
- **Anti-herding (euphoria filter)**: massive green candles with high volume = retail euphoria. Trail tighter or skip new entries after euphoria bars.
- **Volatility of volatility**: rolling std of rolling vol. Vol-of-vol spikes precede regime changes. Use as entry/exit timing.
- **Acceleration (second derivative)**: rate of change of rate of change. Momentum acceleration = early trend, deceleration = late trend. Enter on acceleration, exit on deceleration.
- **Range expansion ratio**: today's range vs N-day average range. Extreme expansion after contraction = trend initiation signal.

### Combining families
The strongest strategies may combine one entry signal family with one filter family:
- Trend entry (EMA cross) + regime filter (Hurst or vol regime) 
- Momentum entry (ROC) + microstructure filter (body ratio or volume anomaly)
- Breakout entry (compression) + calendar filter (session hours)
- Statistical entry (autocorrelation) + asymmetric exit (up/down trail stops)

Keep combinations to 2 families max. More = overfitting.

Avoid local search traps:
- do not spend many iterations only nudging thresholds or lookbacks,
- if several experiments fail in the same family, pivot to a different family,
- prefer changing one structural dimension over micro-tuning many parameters.

Judge ideas by these questions:
- Why should this make money on BTC perps specifically?
- What regime(s) should it exploit?
- Why should it survive unseen periods rather than only one era?
- Which evaluation bottleneck is it meant to improve?
- Is the rule simple enough to generalize?

## Current best
best_composite: 0.875
best_strategy: shadow_asym_range_v1
best_description: EMA 20/50 + asymmetric HH(8)/HL(10) + shadow filter(0.40) + bar-range tanh sizing(0.0-0.10) + trail(1.95%) + time exit(66 bars, cut if <1.5% gain). Key: range sizing > vol sizing, asymmetric structure lookbacks, time exit cuts losers not winners.

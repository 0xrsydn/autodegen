# autodegen — Product Requirements Document

> **Status:** Draft v0.2 | **Target:** greencloud-vps | **Horizon:** v0 (no live trading, no GPU)

---

## Vision

An autonomous agent loop that continuously discovers, backtests, and paper-trades quantitative strategies across crypto spot, perpetual futures, and prediction markets — running 24/7 without human babysitting.

Think of it as a quant research desk where the analyst never sleeps, never gets bored, never anchors to their last idea, and doesn't mind being wrong 49 times in a row if the 50th one works. The agent explores strategy space the way evolution explores fitness landscapes: generate variation, apply selection pressure, keep what survives.

This is Karpathy's autoresearch pattern applied to financial markets. The key insight is the same: separate the immutable oracle (data + backtester + eval) from the mutable sandbox (strategy code), use git as the experiment ledger, and let an LLM agent run the loop.

**The edge here is research breadth, not execution speed.** We are explicitly NOT building a high-frequency or latency-sensitive system. Minimum bar size is 30 minutes. The agent runs overnight and explores more strategy space than a human researcher can in a week. That's the moat.

---

## Problem Statement

Manual quant research is slow, biased, and doesn't scale:

- **Speed**: A human researcher can test maybe 5-10 strategy variants per day. An agent can test 50+ per overnight run.
- **Bias**: Humans anchor. Once a strategy "looks good," they stop searching nearby territory. Agents don't.
- **Narrative capture**: Humans write code to confirm hypotheses. Agents are told the eval function — they don't care about the story.
- **Consistency**: Research quality degrades at 2am, after a bad trade, after three weeks of flat PnL. Agents don't have bad days.
- **Breadth vs depth**: No human can simultaneously explore momentum, mean reversion, funding arb, and cross-asset plays across 20 pairs. Swarms can.

The standard response to this is "just use ML." That requires labeled data, GPUs, and careful feature engineering — and it has its own overfitting failure modes that are harder to detect. This project takes a different path: use LLMs as strategy proposers and keep the eval pipeline brutally honest.

---

## Strategic Focus

**This is a medium-to-higher timeframe research system.** The focus areas are:

- **30m–4h crypto trading**: directional momentum, mean reversion, funding rate carry
- **1h+ Polymarket/prediction market event prediction**: odds mispricing, information arbitrage
- **Portfolio optimization and rebalancing**: consistent returns over speed

**What we are NOT building:**
- HFT or latency-sensitive execution systems
- Sub-minute tick data strategies
- Orderbook microstructure strategies (L2 depth, queue position, latency arb)
- Cross-venue execution arbitrage (too fast, wrong tools)

**Why?** Edge in sub-second markets requires co-location, kernel bypass networking, and FPGA execution — a completely different stack. Our edge is in research quality and strategy robustness, not execution speed. A 30m-bar strategy that works across 8 walk-forward folds and multiple market regimes is more valuable than a microsecond arbitrage we can't execute anyway.

Minimum bar timeframe: **30 minutes**. No tick data, no order flow, no L1/L2 microstructure features.

---

## Core Concept: Autoresearch for Markets

The autoresearch pattern has five components. Here's how they map to autodegen:

| Autoresearch Concept | autodegen Implementation |
|---|---|
| Immutable oracle | `autodegen/oracle/` — data pipeline, backtester, eval (agent cannot modify) |
| Mutable sandbox | `autodegen/sandbox/strategy.py` — agent edits only this file |
| Agent loop | LLM reads context, proposes hypothesis, edits strategy, runs backtest |
| Git as ledger | Every experiment is a git commit; bad experiments are `git reset --hard` |
| Fixed eval budget | Each experiment gets the same data window and time budget — no cherry-picking |

The oracle is sacred. If the agent could modify the backtester, it would find ways to cheat. If it could modify the eval function, it would optimize for something other than actual trading performance. These files are read-only to the agent — **enforced mechanically by Docker readonly container mounts during strategy execution, not just by instruction** (see Sandbox Enforcement section).

The sandbox is the only variable. The agent's entire job is to write better `strategy.py` implementations.

Git as ledger means every experiment is traceable. Commit message = hypothesis. Commit hash = experiment ID. Branch history = research trajectory. You can always reconstruct what was tried and why.

---

## Config System: `config.md`

> **New in v0.2.** This replaces scattered hardcoded defaults.

`config.md` is a human-editable markdown file at the repo root that controls research loop parameters. The agent reads it at the **start of every iteration** and adjusts its exploration accordingly. Human can edit it anytime — changes take effect on the next loop iteration.

```markdown
# autodegen config

## Trading Universe
- pairs: BTC/USDT, ETH/USDT
- exchanges: binance
- timeframes: 1h, 4h

## Risk Constraints
- max_leverage: 3x
- max_position_pct: 25%
- max_drawdown_tolerance: 20%

## Eval Settings
- min_sharpe: 1.0
- min_trades: 50
- walk_forward_folds: 8
- validation_pct: 15%    # promotion gate (agent sees pass/fail)
- test_pct: 15%           # human-only final gate (agent never sees)

## Research Directives
- focus: momentum, mean reversion
- avoid: HFT, orderbook microstructure
- complexity_budget: 8 parameters max
```

**Why markdown and not YAML/TOML?** Because humans edit it, agents read it, and it's version-controlled alongside the experiment ledger. Markdown is self-documenting. A YAML parse error silently breaks the loop; a malformed markdown section is just text.

The agent parses this file on every iteration. If it can't find a directive, it uses a sensible default. Malformed config = log warning, use defaults, continue — never crash the loop over a config file.

---

## Anti-Overfitting Defenses

> This section comes before data sources and features because it's the most important thing in this document. Every backtesting system that's been "working great" and then fails in production failed because of overfitting. This one won't.

### 1. Walk-Forward Validation (mandatory, not optional)

The agent **never** evaluates on the full data window. Data is split into N folds using an **expanding window** approach:

```
fold 1: train=[fold_0]              test=[fold_1]
fold 2: train=[fold_0, fold_1]      test=[fold_2]
fold 3: train=[fold_0..fold_2]      test=[fold_3]
...
fold N-1: train=[fold_0..fold_N-2]  test=[fold_N-1]
```

Implementation (correct expanding-window):
```python
fold_size = len(data) // n_folds

for i in range(1, n_folds):        # start from fold 1
    train_data = data[:i * fold_size]           # everything before fold i
    test_data = data[i * fold_size:(i+1) * fold_size]
    
    strategy = strategy_cls()
    strategy.initialize(train_data)
    result = run_backtest(strategy, test_data, BacktestConfig())
    results.append(compute_metrics(result))
```

First fold trains on fold_0, tests on fold_1. Last fold trains on folds 0..N-2, tests on fold N-1. Training set grows with each fold — this is correct and reflects how a strategy would actually accumulate history.

A Sharpe of 2.5 on full-window backtest means nothing. A Sharpe of 1.1 averaged across 8 walk-forward folds means something.

### 2. Three-Way Data Split (no leakage)

Data is split into three non-overlapping segments:

```
|── Training folds (70%) ──|── Validation holdout (15%) ──|── Test holdout (15%) ──|
        ↑                              ↑                              ↑
   walk-forward runs here      promotion gate (agent sees          human review only
                                pass/fail, not details)           agent never sees this
```

- **Training folds**: Walk-forward cross-validation happens here. Agent iterates freely.
- **Validation holdout**: Used as the promotion gate. Agent sees pass/fail, not the metrics details. This is the "out-of-sample" check before a strategy enters the candidate pool.
- **Test holdout**: **The agent never sees this data and never sees results from it.** Human reviews final strategy candidates against test holdout. This is the last line of defense. If the agent had access to validation holdout results, it would indirectly optimize against them over many experiments. The test holdout prevents that.

**Why the three-way split matters:** An agent running 200 experiments against a fixed validation holdout will eventually find strategies that happen to fit that specific period. The test holdout is never touched during research — only when a human is ready to make a final deployment decision.

The split is configured in `config.md` via `validation_pct` (15%) and `test_pct` (15%). The remaining ~70% goes to walk-forward folds. With 12 months of walk-forward at 70% of total data, you need roughly 17 months of history before the first meaningful eval run.

### 3. Sharpe Calculation (corrected)

Sharpe is calculated on **trade returns** (entry-to-exit PnL per trade), NOT equity-curve bar-level returns.

**Why?** Bar-level returns on an equity curve create autocorrelation artifacts — open positions produce a smooth "drift" in equity that inflates Sharpe. Trade returns are independent samples with no autocorrelation by construction.

```python
def compute_sharpe(fills: list[Fill], risk_free_rate: float = 0.0) -> float:
    """
    Compute Sharpe from trade returns (entry-to-exit PnL per round trip).
    risk_free_rate: annualized, default 0 for crypto (no risk-free benchmark)
    """
    trade_returns = [f.pnl / f.entry_value for f in fills if f.is_close]
    
    if len(trade_returns) < 2:
        return 0.0
    
    mean_r = np.mean(trade_returns)
    std_r = np.std(trade_returns, ddof=1)
    
    EPSILON = 1e-8
    if std_r < EPSILON:
        return 0.0  # near-zero std: can't distinguish signal from flatline
    
    # Annualize: sqrt(trades_per_year)
    # For 1h bars with average 2-day hold: ~180 trades/year
    # Caller provides annualization factor based on actual trade frequency
    excess_return = mean_r - (risk_free_rate / annualization_factor)
    return (excess_return / std_r) * np.sqrt(annualization_factor)
```

Key properties:
- **Trade-return Sharpe**: each data point is one round-trip trade
- **Near-zero std guard**: returns 0 if std < epsilon (avoids divide-by-zero and spurious infinite Sharpe)
- **Risk-free rate**: configurable, default 0 for crypto (crypto has no risk-free rate that makes sense as a benchmark)
- **Documented explicitly** in results.tsv header and strategy scorecard

### 4. Regime Detection

Markets have regimes. A momentum strategy that crushes in trending markets will lose money in ranging markets.

Every strategy is evaluated across at least three regime types:
- **Bull trend**: sustained upward price movement
- **Bear trend**: sustained downward price movement
- **Crab/ranging**: sideways consolidation, low directional bias

A strategy must show positive composite score in at least 2/3 regimes, and must not show catastrophic failure (MaxDD > 40%) in any regime. Strategies that only work in one regime are flagged in results.tsv.

Regime classification uses a simple rolling-window heuristic (upgradeable to HMM later).

### 5. Simplicity Penalty in the Eval Score (with gaming prevention)

The composite score formula has a `SimplexityBonus` component. Strategies with fewer parameters get a score boost. **This is enforced in math, not just in the prompt.**

**Gaming prevention**: The AST lint pass counts not just `len(strategy.parameters)` but also hardcoded numeric literals in the strategy code that aren't in `self.parameters`. 

```
complexity = len(parameters) + count_hardcoded_numeric_constants(ast(strategy.py))
```

Hardcoded `0.618`, `14`, `0.02` in logic that bypasses `self.parameters` get counted as hidden parameters. An agent can't game the simplicity score by externalizing constants from the dict.

**Exclusion**: Numeric literals inside the `self.parameters = {...}` dict assignment itself are **not** counted — those are the legitimate parameter definitions. Only hardcoded numbers in method bodies (inside `on_bar`, `initialize`, `on_fill`, etc.) count toward complexity.

A 3-parameter strategy with Sharpe 1.2 outscores a 10-parameter strategy with Sharpe 1.3. Complexity is borrowed future performance — you're fitting noise, not edge.

### 6. Minimum Trade Count Threshold

No strategy passes evaluation with fewer than 50 trades in the test window. Period.

With 30m+ bars, 50 trades is achievable even in short test windows. A strategy generating fewer than 50 trades is either holding too long (check if it's actually trading) or the test window is too short.

---

## Data Sources

### Phase 1 (MVP) — Binance

One exchange, well-understood, deep liquidity, ccxt has solid support.

**Binance data collected:**
- **Spot OHLCV**: BTC/USDT, ETH/USDT. 1h, 4h, 1d candles. Historical depth: 2+ years.
- **Perps OHLCV**: BTC/USDT, ETH/USDT perpetuals. Same resolutions.
- **Funding rates**: Historical 8h funding rate snapshots. Forward-filled in the data pipeline (no gaps in the time series — `funding_rate_ffill: true`).

No orderbook snapshots in Phase 1. We're operating on 30m+ bars where L2 microstructure is irrelevant to strategy performance.

**Data quality gate (enforced in ingest):** Every ingested parquet file passes:
- Monotonic timestamps (no out-of-order bars)
- No duplicate timestamps
- OHLC consistency: high >= max(open, close), low <= min(open, close)
- Volume >= 0
- Fail-loud on violations — bad data is quarantined, not silently written

### Phase 2 — Exchange Expansion

After Sprint 4 is stable:
- **Hyperliquid**: On-chain perps with transparent funding. Unique data (vault positions, liquidation data).
- **Bybit**: Redundancy for Binance. Useful for cross-pair signal confirmation.
- **Coinbase**: Spot, different user base composition, useful for funding/spot basis signals.

### Phase 3 — Prediction Markets

The exotic layer. High-alpha, low-liquidity.
- **Polymarket**: Event contracts, binary outcomes, odds history via CLOB API.
- **Kalshi**: US-regulated prediction market, event contracts and historical data.

Prediction market edge:
1. Odds that haven't priced in information derivable from other sources (crypto price → crypto event odds)
2. Mean reversion in odds after sharp moves that overshoot
3. Cross-market correlation plays

Minimum timeframe for prediction market signals: 1h. No intraday microstructure plays.

### Data Versioning and Reproducibility

Every eval run records a `data_snapshot_id` (hash of the parquet files used). The results.tsv row includes:

```
strategy_hash | oracle_hash | data_snapshot_id | config_hash | composite_score | ...
```

This enables full reproducibility: given a results.tsv row, you can reconstruct exactly what data, oracle code, and config produced that score. No ambiguity.

---

## Strategy Families

Five families to explore, in rough priority order. **All strategies operate on 30m+ bars.**

### 1. Momentum
Price/volume trend following. Works until it doesn't, but it works for a long time.

Key variants:
- Dual moving average crossovers (simple baseline, 30m/1h/4h)
- Breakout systems (range breakouts, volume-confirmed)
- Relative strength (cross-pair momentum)
- Funding-weighted momentum (trade in direction of funding rate mean reversion target)

Regime affinity: bull/bear trends. Fails in ranging markets. The eval pipeline catches this.

### 2. Mean Reversion
Price returns to some "fair value." Works in ranging markets, kills you in trends.

Key variants:
- Bollinger band reversion (1h/4h)
- Z-score reversion to rolling mean
- Pairs trading (co-integrated pairs, e.g., BTC/ETH spread)
- Funding rate extremes (mean reversion to zero funding)

Note: Orderbook imbalance reversion is **explicitly excluded** — it requires sub-minute resolution and L2 data we don't collect.

### 3. Funding Rate Carry
A crypto-specific edge that doesn't exist in TradFi. When perpetual funding rates are elevated, there's a systematic carry trade:
- Long spot + short perp = collect funding (when funding is positive)
- Short spot + long perp = collect funding (when funding is negative)

Pure delta-neutral. Risk is sudden funding reversals and liquidation risk on the perp leg. Both modeled in the backtester. This is one of the most durable edges in crypto and plays to our 30m+ timeframe perfectly.

### 4. Statistical Arbitrage (Medium Timeframe)
Not latency arb — statistical relationships that decay over hours, not milliseconds.

Key variants:
- Basis trading: spot vs perp price convergence
- Cross-pair correlation plays (BTC leads ETH, trade the lag)
- Regime-conditioned rebalancing (hold when conditions favor, exit when not)

No execution-speed dependency. These signals persist for hours on 30m/1h bars.

### 5. Prediction Market Edge (Phase 3)

Once Polymarket data is available:
- Cross-market correlation plays (BTC price → crypto event contract odds)
- Odds mean reversion after sharp moves
- Event outcome prediction using price + funding data as features

Evaluation for prediction markets uses expected value (EV) against realized outcomes rather than Sharpe.

---

## Sandbox Enforcement

The oracle is immutable. This is enforced **mechanically**, not just philosophically.

**Execution model**: The agent loop runs on the **host** (not inside Docker). Per-backtest strategy execution is isolated in an ephemeral Docker container (or `systemd-run --scope` on NixOS):

```
Host process: agent-loop (reads config, proposes experiments, manages git)
    ↓ spawns per-backtest
    docker run --rm --read-only --network=none --memory=2g --cpus=2 \
      -v ./autodegen/sandbox/strategy.py:/app/strategy.py:ro \
      -v ./data:/data:ro \
      -v ./output:/output:rw \
      autodegen-runner python -m autodegen.oracle.backtest
    ↓ reads results from output/
    ↓ keeps or reverts
```

- `autodegen/oracle/` is **baked into the Docker image** — not mounted at runtime. The container literally cannot access oracle source.
- `autodegen/sandbox/strategy.py` is mounted **read-only** in backtest containers — it's read but cannot be modified by the container.
- `data/` is mounted **read-only** — no data exfiltration or corruption from inside the container.
- `--network=none` — no network access during backtest. No calling home, no API calls, no exfil.
- `--read-only` filesystem — only explicitly mounted paths are writable.
- No `docker.sock` mount anywhere — that's root-equivalent host access, never needed for git ops.
- Resource limits per backtest: `--memory=2g`, `--cpus=2`.
- Timeout: subprocess killed after 15 minutes.

**Docker compose is for data services only** (`docker-compose.yml` defines the `data-ingester` cron and `paper-trader`). The agent loop runs as a host process or systemd service.

**Oracle hash verification**: At each eval run, the oracle hash (SHA256 of `autodegen/oracle/` directory tree) is computed and stored in results.tsv. This makes tampering detectable even during local development without Docker.

---

## Eval Metrics

**Primary metric: Sharpe Ratio**
Trade-return Sharpe (see Anti-Overfitting section). Walk-forward averaged, not full-period. Target: > 1.0 to pass backtest gate. Risk-free rate: 0.0 (default for crypto).

**Secondary metrics:**
- **Max Drawdown (MaxDD)**: Peak-to-trough decline. Hard limit: < 30%.
- **Calmar Ratio**: Annualized return / MaxDD. Target: > 0.5.
- **Win Rate**: Informational only. A 40% win rate with 3:1 R:R beats 70% win rate with 1:2.
- **Trade Count**: Must exceed 50 in test window. Hard gate.
- **Parameter Count + Hardcoded Constants**: Used in simplicity penalty (see gaming prevention above).

**Composite Score formula:**
```
composite = 0.4 * sharpe_norm 
          + 0.3 * (1 - maxdd) 
          + 0.2 * calmar_norm 
          + 0.1 * simplicity_bonus
```

Where:
- `sharpe_norm` = Sharpe capped and normalized to [0,1] (cap at 3.0)
- `simplicity_bonus` = `max(0, 1 - complexity / 10)` where complexity = len(parameters) + hardcoded constant count

A strategy scoring 0.5+ is worth keeping. A strategy scoring 0.7+ is a strong candidate.

---

## Promotion Pipeline

```
[backtest loop]
     │
     ▼
[walk-forward pass?] ──NO──▶ [discard, git reset --hard, log error to results.tsv]
     │YES
     ▼
[validation holdout pass?] ──NO──▶ [discard]
     │YES
     ▼
[candidate pool]
     │
     ▼
[paper trade: 7 days live data]
     │
     ▼
[paper metrics match backtest within tolerance?] ──NO──▶ [flag as overfit, archive]
     │YES
     ▼
[HUMAN REVIEWS: test holdout results + strategy logic + equity curve]
     │
     ▼
[live trading] ← MANUAL GATE. No auto-deploy. Ever.
```

**Error handling**: If eval fails due to a malformed strategy (syntax error, runtime exception, infinite loop), the agent loop automatically:
1. Catches the exception
2. Runs `git reset --hard HEAD~1` to remove the broken code
3. Logs the error to results.tsv with `status="error"`
4. Continues to the next iteration

No infinite restart loops from malformed strategies. One bad strategy = one discarded commit + one error row in results.tsv.

---

## Strategy Interface

### v0: Single-Symbol `on_bar()`

```python
def on_bar(self, bar: Bar, portfolio: Portfolio) -> list[Signal]:
    ...
```

One symbol, one bar at a time. Straightforward.

### Phase 2: Multi-Symbol Upgrade Path

For multi-asset strategies (portfolio optimization, cross-pair momentum), the interface will add:

```python
def on_universe_bar(self, bars: dict[str, Bar], portfolio: Portfolio) -> list[Signal]:
    """
    Called with all symbols' bars simultaneously (all aligned to same timestamp).
    Returns signals across any symbol in bars.
    """
    ...
```

**This is not implemented in Sprint 0-3.** Document it now so the architecture supports it later. The single-symbol `on_bar()` remains the primary interface for the first 4 sprints. Multi-symbol is an additive change, not a breaking one.

---

## Agent Swarm (Future)

v0 runs a single agent exploring one strategy family at a time. Natural extension:

- Agent A: momentum strategies on BTC/ETH (1h/4h)
- Agent B: funding rate carry strategies
- Agent C: mean reversion on mid-cap pairs (4h)
- Agent D: prediction market plays (Phase 3)

Each agent maintains its own branch. A coordinator reviews branches and merges best candidates. Research throughput scales linearly with agent count.

Constraint: greencloud-vps has 12GB RAM. Each agent runs as a host process (lightweight), but spawns backtest containers with `--memory=2g`. At steady state with 3 parallel backtests: 6GB for workloads, leaving headroom for the OS and data services.

---

## Target Infrastructure

**greencloud-vps** (Singapore):
- 6 vCPU AMD EPYC
- 12GB RAM
- 110GB disk
- No GPU

Design constraints:
- All ML/optimization must be CPU-based and fast (no model training)
- Market data stored as parquet (columnar compression, fast scan)
- Backtester must complete a 2-year 1h OHLCV backtest in < 30 seconds
- No more than 3 agents running in parallel at steady state (4g mem each)
- Total disk budget for market data: ~50GB

---

## Tech Stack

| Component | Choice | Rationale |
|---|---|---|
| Language | Python 3.12 | Ecosystem. No debate. |
| Dependency mgmt | uv | Fast, lockfile-first |
| Exchange APIs | ccxt | Unified interface, all major exchanges |
| Data storage | Parquet + SQLite | Parquet for time series, SQLite for state |
| Dataframe ops | polars | Faster than pandas, better memory use |
| Backtester | Custom (`autodegen/oracle/backtest.py`) | 500 lines, fully auditable |
| Agent | Claude API | LLM with function calling |
| Config | config.md | Human-editable markdown, version controlled |
| Git | Standard git | Experiment ledger |
| Deployment | Docker + NixOS/systemd | Dual backend: Docker for open source quickstart, NixOS/systemd for production; `docker-compose.yml` for data services only |

**Why not backtrader/vectorbt?** Custom event-driven backtester = no magic, no hidden state. 500 lines you understand beats 50,000 lines you trust.

**Why polars over pandas?** 5-10x faster for rolling window ops on 2+ years of 30m bars. Worth the minor API learning curve.

---

## Extensibility Vision: Pluggable Domain Architecture

> **Design principle, not afterthought.** The oracle/sandbox pattern is domain-agnostic. Crypto spot/perps trading is the first "research track" — not the only one.

The core infrastructure (agent loop, git ledger, sandbox runner, config system, eval framework) is shared across all research tracks. Adding a new domain means adding a new oracle module and strategy interface. It does **not** mean rewriting the loop.

**Planned research tracks:**

| Track | Timeframe | Sprint |
|---|---|---|
| Crypto spot/perps (BTC, ETH, funding carry) | 30m–4h | Sprint 0–5 |
| Prediction markets (Polymarket, Kalshi) | 1h+ | Sprint 7 |
| DeFi portfolio optimization (LP rebalancing, yield farming, IL management) | 4h+ | Future |
| MEV extraction simulation (sandwich, arb, liquidation) | Block-level | Future |
| Funding rate carry (cross-venue) | 8h | Future |
| Options/structured products | 1h+ | Future |
| Macro regime allocation (BTC/ETH/stables/DeFi rotation) | 1d | Future |

**Each research track provides:**
1. **Domain oracle** — data ingest + domain-specific backtester + eval metrics suited to the domain (Sharpe for trading, EV for prediction markets, etc.)
2. **Strategy interface** — extends the base `Strategy` contract with domain-specific bar types and signals
3. **Config section** — domain-specific parameters appended to `config.md`

**What stays shared (never rewritten):**
- Agent loop (`autodegen/agent_loop.py`)
- Git commit/reset ledger
- `SandboxRunner` abstraction (Docker + systemd backends)
- `config.md` + `program.md` + `results.tsv` schema
- Composite score formula and hard gates

The oracle/sandbox boundary is the key abstraction. The oracle defines the rules of the game — what data is available, how performance is measured, what's a valid signal. The sandbox is where the agent plays. Swap the oracle, get a different game. The agent loop doesn't change.

---

## Non-Goals for v0

Explicitly out of scope. Not because they're bad ideas — scope creep kills projects.

1. **ML model training**: No GPU, no labeled datasets. The agent is the "model." Strategy logic is rule-based.
2. **Live trading**: Paper trading only. No capital at risk in v0.
3. **Multi-asset portfolio optimization**: Each strategy trades one or a few pairs in v0. Portfolio-level Kelly sizing is a Phase 2+ problem.
4. **Sub-30-minute strategies**: Minimum bar size is 30 minutes. No tick data, no L2 orderbook, no microstructure. If a strategy requires sub-minute execution, it's out of scope permanently.
5. **Latency arbitrage**: Wrong tools, wrong infrastructure. This is not a co-location play.
6. **Derivatives pricing models**: No Black-Scholes, no options greeks. Perps only for derivatives.
7. **Alternative data (Phase 1)**: No Twitter sentiment, no on-chain whale tracking in Sprint 0-3. Keep data sources clean and reproducible.

---

## Success Criteria

The system is working when:

1. **Throughput**: Agent runs overnight (8+ hours) and produces 50+ strategy experiments in results.tsv
2. **Quality gate**: At least 1 strategy per overnight run passes both walk-forward AND validation holdout gates
3. **Paper trade confirmation**: At least 1 strategy per week passes paper trading with metrics within 20% of backtest
4. **No crashes**: System runs unattended. Data ingestion is reliable. Backtester handles edge cases (zero volume bars, missing data, funding rate gaps) without throwing exceptions. Malformed strategies are caught and discarded gracefully.
5. **Reproducibility**: Given a results.tsv row (strategy_hash + oracle_hash + data_snapshot_id + config_hash), we can reproduce the exact backtest result. Same data, same code, same output. Always.

Success is NOT:
- A specific Sharpe number
- A specific profit target (paper trading only in v0)
- Agent generating "impressive-sounding" hypotheses (we care about composite scores)
- Speed (we care about research depth, not execution latency)

---

## Open Questions

- **LLM choice for agent**: Claude Sonnet for the loop (cost-efficient at 50+ experiments/night), Opus for strategy review? Leaning Sonnet loop + occasional Opus review pass.
- **Experiment budget**: Fixed wall-clock time (5 min) or fixed data window? Fixed data window for reproducibility. Strategy execution killed by subprocess timeout if it exceeds budget.
- **Regime classification method**: Simple rolling-window heuristic to start. HMM upgrade in Sprint 4+ if needed.
- **Minimum backtest data window**: 12 months walk-forward folds + 15% validation holdout + 15% test holdout. At 70% train ratio, total is ~17 months minimum data before the first meaningful eval run.
- **When to expand to Phase 2 exchanges**: After Sprint 4 stable and ≥ 3 strategies have passed paper trading.
- **config.md parse failures**: Log warning and use defaults. Never crash loop over malformed config.

# autodegen — System Design

> **Status:** Draft v0.2 | This document describes the technical implementation. Read PRD.md first for context and rationale.

---

## Repository Layout

```
autodegen/
├── autodegen/              # Python package
│   ├── __init__.py
│   ├── oracle/             # immutable — data pipeline, backtester, evaluator
│   │   ├── __init__.py
│   │   ├── ingest.py       # data pipeline — fetch, clean, store
│   │   ├── backtest.py     # immutable event-driven backtester
│   │   └── evaluate.py     # multi-metric scorer, walk-forward, regime detection
│   ├── sandbox/            # agent-mutable
│   │   ├── __init__.py
│   │   ├── strategy.py     # THE ONLY FILE THE AGENT EDITS
│   │   └── runner.py       # SandboxRunner abstraction (Docker + systemd backends)
│   ├── agent_loop.py
│   └── paper_trader.py
├── data/                   # local data store (parquet + SQLite)
│   ├── ohlcv/              # partitioned by exchange/pair/resolution
│   ├── funding/            # funding rate history
│   └── state.db            # SQLite — experiment state, paper trade state
├── docs/
│   ├── PRD.md
│   └── SYSTEM-DESIGN.md
├── config.md               # human-editable research config (read by agent each iteration)
├── program.md              # agent firmware — directives, constraints, loop protocol
├── results.tsv             # experiment ledger (gitignored — tracks all runs)
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

**CLI commands:**
```bash
uv run python -m autodegen.oracle.ingest    # data pipeline
uv run python -m autodegen.oracle.backtest  # run a backtest directly
uv run python -m autodegen.oracle.evaluate  # eval a strategy
uv run python -m autodegen.agent_loop       # start the research loop
uv run python -m autodegen.paper_trader     # start paper trading
```

**The `autodegen/oracle/` directory is read-only to the agent.** Enforced **mechanically** — oracle source is baked into the Docker image and not mounted in backtest containers. For the systemd backend, the path is marked `ReadOnlyPaths=` in the transient scope. Oracle hash is verified at each eval run and stored in results.tsv.

The research ledger is preserved in git history. Every experiment = one commit. Commit message = hypothesis. `git reset --hard HEAD~1` removes bad code, but the results.tsv entry persists (gitignored), preserving full history of what was tried.

---

## Config System (`config.md`)

The agent reads `config.md` at the **start of every loop iteration**. This is the human steering interface for the research loop — change it anytime, takes effect immediately.

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

## Deployment
- sandbox_backend: docker  # options: "docker" | "systemd"
```

The `sandbox_backend` key determines which `SandboxRunner` implementation is instantiated at loop startup. Default is `docker` for open source compatibility; set to `systemd` on NixOS deployments.

The agent loop parses this with a simple markdown parser. Parse failures log a warning and fall back to defaults — the loop never crashes over a malformed config. The config is version controlled alongside experiment history.

A `config_hash` (SHA256 of the config.md contents) is recorded in each results.tsv row for reproducibility.

---

## Data Pipeline (`autodegen/oracle/ingest.py`)

### Design Principles

- **Idempotent**: Re-running ingest never creates duplicate data.
- **Incremental**: Only fetches new data since last run. Cron-friendly.
- **Schema-stable**: Parquet schema never changes without a migration script.
- **Fail-loud**: Missing data is an error, not a silent zero.
- **Data quality gate**: Every ingested file passes validation before writing.

### Data Quality Validation

Every ingested batch is validated before writing to parquet:

```python
def validate_ohlcv(df: pl.DataFrame) -> None:
    """
    Fail-loud on data quality violations. Bad data is quarantined, not written.
    """
    # 1. Monotonic timestamps
    if not df["timestamp"].is_sorted():
        raise DataQualityError("Non-monotonic timestamps detected")
    
    # 2. No duplicate timestamps
    if df["timestamp"].n_unique() != len(df):
        raise DataQualityError(f"Duplicate timestamps: {len(df) - df['timestamp'].n_unique()} dupes")
    
    # 3. OHLC consistency
    if (df["high"] < df["open"]).any() or (df["high"] < df["close"]).any():
        raise DataQualityError("High < open or close: OHLC relationship violated")
    if (df["low"] > df["open"]).any() or (df["low"] > df["close"]).any():
        raise DataQualityError("Low > open or close: OHLC relationship violated")
    
    # 4. Volume >= 0
    if (df["volume"] < 0).any():
        raise DataQualityError("Negative volume detected")
```

On violation: log error, quarantine the batch (write to `data/quarantine/`), continue — don't write bad data to the main store.

### CEX Data via ccxt

```python
# Pseudocode — actual implementation in autodegen/oracle/ingest.py
import ccxt
import polars as pl

def fetch_ohlcv(exchange_id: str, symbol: str, timeframe: str, 
                since: int, limit: int = 1000) -> pl.DataFrame:
    exchange = ccxt.binance({"enableRateLimit": True})
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
    df = pl.DataFrame(ohlcv, schema=["timestamp", "open", "high", "low", "close", "volume"])
    validate_ohlcv(df)  # fail-loud before returning
    return df

def fetch_funding_history(exchange_id: str, symbol: str, since: int) -> pl.DataFrame:
    # ccxt unified: exchange.fetch_funding_rate_history()
    # Returns: timestamp, fundingRate, fundingTimestamp
    # After fetch: forward-fill any gaps (funding_rate_ffill: true)
    ...
```

**Funding rate handling**: Binance 8h funding snapshots are forward-filled in the data pipeline. Gaps in funding rate data (missing periods) are filled with the most recent known value, not zero. This is documented explicitly in schema notes: `funding_rate_ffill: true`.

### Parquet Storage Schema

All time series stored as parquet, partitioned by `exchange/symbol/timeframe/`:

```
data/ohlcv/binance/BTC-USDT/1h/2024-01.parquet
data/ohlcv/binance/BTC-USDT/1h/2024-02.parquet
...
data/funding/binance/BTC-USDT/2024-01.parquet
...
```

**OHLCV schema:**
```
timestamp:       Int64       # Unix ms, UTC — strictly monotonic, no duplicates
open:            Float64
high:            Float64     # >= max(open, close), always
low:             Float64     # <= min(open, close), always
close:           Float64
volume:          Float64     # base asset volume (BTC/ETH etc.), always >= 0 — ccxt default
quote_volume:    Float64     # optional: quote volume (USDT) — null if not needed
funding_rate:    Float64     # null for spot; merged + forward-filled for perps
                             # funding_rate_ffill: true (gaps filled with last known value)
```

No orderbook/L2 data in the schema. We operate on 30m+ bars where L2 microstructure is irrelevant to strategy performance and adds substantial storage cost.

**Incremental update flow:**
```
1. Read last timestamp from existing parquet for this exchange/symbol/timeframe
2. Fetch from (last_timestamp + 1) to now
3. Validate batch (data quality gate)
4. Append to current month's parquet (or create new month file)
```

### Data Versioning

Each eval run records a `data_snapshot_id`:

```python
def compute_data_snapshot_id(data_dir: Path) -> str:
    """SHA256 of all parquet file paths + their actual contents (content hash, not mtime)."""
    hasher = hashlib.sha256()
    for f in sorted(data_dir.glob("**/*.parquet")):
        hasher.update(f.relative_to(data_dir).as_posix().encode())
        hasher.update(f.read_bytes())  # actual content hash — mtime is not reproducible
    return hasher.hexdigest()[:16]
```

Results row includes: `strategy_hash | oracle_hash | data_snapshot_id | config_hash | ...`

This enables full reproducibility — any results.tsv row can be reconstructed given the hashes.

### Polymarket Data (Phase 3)

Polymarket exposes a CLOB API. Minimum signal timeframe: 1h.

```
GET https://clob.polymarket.com/markets
GET https://clob.polymarket.com/prices-history?market={condition_id}&interval=1h
```

Schema for prediction market data:
```
timestamp:       Int64
condition_id:    Utf8        # unique market identifier
question:        Utf8        # human-readable event description
yes_price:       Float64     # implied probability 0-1
no_price:        Float64
volume_24h:      Float64
open_interest:   Float64
resolution:      Utf8        # "yes", "no", "unresolved"
```

Prediction market evaluation uses EV (expected value against outcome) rather than Sharpe. Different eval pipeline, different oracle module. Phase 3 problem.

---

## Backtester (`autodegen/oracle/backtest.py`)

> **The backtester is the integrity of this entire system.** A biased backtester produces biased results. An LLM agent optimizing against a biased backtester produces beautifully-fitted nonsense. Every design decision here minimizes simulation bias.

### Architecture: Event-Driven

One bar at a time, in chronological order. Strategy receives only data it would have had in real-time. No future data leaks.

```python
def run_backtest(strategy: Strategy, data: pl.DataFrame, config: BacktestConfig) -> BacktestResult:
    portfolio = Portfolio(initial_cash=config.initial_cash)
    fills = []
    pending_signals = []  # signals from previous bar, executed at current bar's open
    
    for i, bar in enumerate(data.iter_rows(named=True)):
        bar_obj = Bar(**bar)
        
        # 1. Execute pending signals from PREVIOUS bar at current bar's open
        #    This is the one-bar delay: strategy sees bar N close, fills at bar N+1 open.
        for signal in pending_signals:
            fill = execute_order(signal, bar_obj.open, bar_obj, portfolio, config)
            if fill:
                portfolio.apply_fill(fill)
                strategy.on_fill(fill)
                fills.append(fill)
        pending_signals = []
        
        # 2. Strategy observes current bar (OHLCV complete) and emits NEW signals
        new_signals = strategy.on_bar(bar_obj, portfolio)
        pending_signals = new_signals  # queued for NEXT bar's open
        
        # 3. Mark-to-market at close
        portfolio.update_equity(bar_obj.close)
        
        # 4. Funding payments for perps (every 8h)
        if is_funding_time(bar_obj.timestamp):
            funding = calculate_funding_payment(portfolio, bar_obj)
            portfolio.apply_funding(funding)
    
    return BacktestResult(portfolio=portfolio, fills=fills)
```

**Performance target:** 2-year backtest on 1h bars (17,520 bars) < 5 seconds. For 30m bars (35,040 bars) < 10 seconds. Acceptable for overnight runs of 50+ experiments.

### Look-Ahead Bias Prevention

**Rules enforced in backtest.py:**

1. **One-bar execution delay**: `strategy.on_bar(bar_N)` returns a signal → signal is queued in `pending_signals` → fill executes at OPEN of bar N+1. The strategy sees the complete OHLCV of bar N, decides, fills at open of bar N+1. This is honest. Implemented via the pending order queue in the main loop (see pseudocode above).

2. **No future data in Bar object**: The `Bar` passed to `on_bar` contains only OHLCV for the current bar. No access to future rows.

3. **Strategy self-buffering**: The framework does not provide historical data access. Strategies maintain their own lookback buffers.

4. **Audit mode**: `BacktestConfig(audit=True)` flags any fill timestamped before its signal bar. Zero tolerance.

### Slippage Model

For 30m+ bars with bar-volume linear impact, slippage modeling is simple and sufficient:

```python
def calculate_slippage(signal: Signal, bar: Bar, config: SlippageConfig) -> float:
    """
    Bar-volume linear impact. Appropriate for 30m+ bars.
    No microstructure model needed at these timeframes.
    
    Units are consistent: signal.size and bar.volume are both in BASE asset units
    (BTC, ETH, etc.) — this is what ccxt returns by default.
    participation_rate is dimensionless: base_units / base_units.
    """
    # Both signal.size and bar.volume are base asset units (e.g., BTC)
    participation_rate = signal.size / bar.volume  # dimensionally correct
    impact_bps = config.impact_factor * participation_rate * 10000
    impact_bps = min(impact_bps, config.max_slippage_bps)
    
    direction = 1 if signal.side == "buy" else -1
    return bar.open * (impact_bps / 10000) * direction
```

Default config: `impact_factor=0.1`, `max_slippage_bps=50` (0.5%).

This is the right model for our timeframe. BTC/USDT on Binance with 30m bars has enormous volume — for any reasonable strategy position size, participation rate is tiny and slippage is a rounding error. The model handles it correctly and is easy to audit.

### Fee Model

```python
@dataclass
class FeeConfig:
    maker_fee_bps: float = 2.0    # 0.02%
    taker_fee_bps: float = 4.0    # 0.04%
    funding_rate_factor: float = 1.0
```

Binance perps defaults: maker 2bps, taker 4bps. Funding payments at 8h intervals, using forward-filled rates from the data pipeline.

Strategies that only "work" before fees are not real strategies.

### Position Sizing and Risk Controls

```python
@dataclass
class RiskConfig:
    max_position_pct: float = 0.25    # max 25% of portfolio in a single position
    max_leverage: float = 3.0          # max leverage for perps
    min_margin_ratio: float = 0.05     # liquidation trigger
    max_order_size_pct: float = 0.25   # max single order as % of portfolio
```

Default `max_position_pct` is 25% (matching config.md default). Orders exceeding limits are clipped. Liquidation simulation: position force-closed at current close if margin falls below threshold.

---

## Eval Pipeline (`autodegen/oracle/evaluate.py`)

### Walk-Forward Implementation (Expanding Window)

```python
def walk_forward_eval(
    strategy_cls: type,
    data: pl.DataFrame,
    n_folds: int = 8,
) -> WalkForwardResult:
    """
    Expanding-window walk-forward cross-validation.
    
    fold i trains on data[:i*fold_size], tests on data[i*fold_size:(i+1)*fold_size]
    
    First fold: train=fold_0, test=fold_1
    Last fold: train=fold_0..fold_(N-2), test=fold_(N-1)
    
    NOTE: The final holdout segments are never passed to this function.
    Caller is responsible for slicing data to the walk-forward window only.
    """
    fold_size = len(data) // n_folds
    results = []
    
    for i in range(1, n_folds):           # start from fold 1 (need fold 0 as first train)
        train_data = data[:i * fold_size]  # everything before fold i — expands each iteration
        test_data = data[i * fold_size:(i + 1) * fold_size]
        
        strategy = strategy_cls()
        strategy.initialize(train_data)
        result = run_backtest(strategy, test_data, BacktestConfig())
        results.append(compute_metrics(result))
    
    return WalkForwardResult(
        fold_results=results,
        avg_sharpe=mean([r.sharpe for r in results]),
        avg_maxdd=mean([r.max_drawdown for r in results]),
        avg_calmar=mean([r.calmar for r in results]),
        worst_fold_sharpe=min([r.sharpe for r in results]),
    )
```

**Note on fold indexing:** With 8 folds, we run 7 train/test pairs (folds 1-7). Fold 0 is always training-only (the seed for the expanding window). This is correct — don't start at `range(0, n_folds-1)` with fixed windows; that's not expanding-window and understates the training data available in later folds.

`worst_fold_sharpe` matters: Sharpe 1.5 average with one fold at -0.8 is suspicious. Sharpe 1.2 average with worst fold at 0.6 is trustworthy.

### Three-Way Data Split

```python
def split_data_three_way(
    data: pl.DataFrame,
    validation_pct: float = 0.15,
    test_pct: float = 0.15,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """
    Returns: (walk_forward_data, validation_holdout, test_holdout)
    
    walk_forward_data: agent iterates here freely
    validation_holdout: promotion gate — agent sees pass/fail only
    test_holdout: human review only — agent NEVER sees results from this
    """
    n = len(data)
    test_start = int(n * (1 - test_pct))
    val_start = int(n * (1 - validation_pct - test_pct))
    
    return (
        data[:val_start],
        data[val_start:test_start],
        data[test_start:],   # never passed to agent loop
    )
```

The test holdout data file path is never given to the agent's eval command. It exists only for the human review stage.

### Metrics Computation (Trade-Return Sharpe)

```python
def compute_metrics(result: BacktestResult) -> Metrics:
    """
    Sharpe is computed from trade returns (entry-to-exit PnL per round trip),
    NOT from equity curve bar-level returns. This avoids autocorrelation
    artifacts from open positions drifting the equity curve.
    """
    fills = [f for f in result.fills if f.is_close]  # round-trip closes only
    
    if len(fills) < 2:
        return Metrics(sharpe=0.0, ...)  # not enough trades
    
    trade_returns = [f.pnl / f.entry_value for f in fills]
    mean_r = np.mean(trade_returns)
    std_r = np.std(trade_returns, ddof=1)
    
    EPSILON = 1e-8
    if std_r < EPSILON:
        sharpe = 0.0  # near-zero std: strategy is not trading or all trades identical
    else:
        # Annualize using sqrt(trades_per_year)
        # trades_per_year estimated from actual trade frequency in this result
        annualization_factor = estimate_annualization_factor(fills)
        risk_free_rate = result.config.risk_free_rate  # default 0.0 for crypto
        excess_return = mean_r - (risk_free_rate / annualization_factor)
        sharpe = (excess_return / std_r) * np.sqrt(annualization_factor)
    
    # Max drawdown (still computed from equity curve — this is correct)
    equity = np.array(result.portfolio.equity_curve)
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak
    max_dd = abs(drawdown.min())
    
    total_return = (equity[-1] / equity[0]) - 1
    annualized_return = (1 + total_return) ** (365 / result.days_elapsed) - 1
    calmar = annualized_return / max_dd if max_dd > 0 else 0
    
    return Metrics(
        sharpe=sharpe,           # trade-return Sharpe, risk-free rate adjusted
        max_drawdown=max_dd,
        calmar=calmar,
        win_rate=len([f for f in fills if f.pnl > 0]) / len(fills),
        trade_count=len(fills),
        total_return=total_return,
    )
```

**Sharpe is trade-return Sharpe** — documented in results.tsv column header and strategy scorecard. No ambiguity about which Sharpe variant is being reported.

### Composite Score (with AST Complexity)

```python
def composite_score(metrics: WalkForwardResult, strategy: Strategy) -> float:
    sharpe_norm = min(max(metrics.avg_sharpe, 0), 3.0) / 3.0
    dd_score = max(0, 1 - metrics.avg_maxdd / 0.30)
    calmar_norm = min(max(metrics.avg_calmar, 0), 2.0) / 2.0
    
    # Complexity = parameters + hardcoded numeric constants in AST
    # Prevents gaming simplicity score by externalizing constants from self.parameters
    param_count = len(strategy.parameters)
    ast_constants = count_hardcoded_numeric_literals(strategy)  # AST lint pass
    complexity = param_count + ast_constants
    simplicity_bonus = max(0, 1 - complexity / 10)
    
    return (
        0.4 * sharpe_norm
        + 0.3 * dd_score
        + 0.2 * calmar_norm
        + 0.1 * simplicity_bonus
    )


def count_hardcoded_numeric_literals(strategy: Strategy) -> int:
    """
    AST lint: count numeric literals in strategy METHOD BODIES only.
    
    Excludes:
    - 0, 1, -1 (common idiom constants)
    - Numeric literals inside `self.parameters = {...}` dict assignment —
      those are the legitimate parameter definitions, not hidden constants.
    
    Only counts hardcoded numbers in on_bar(), initialize(), on_fill(), etc.
    Prevents gaming simplicity score by hardcoding constants outside self.parameters.
    """
    import ast, inspect
    source = inspect.getsource(type(strategy))
    tree = ast.parse(source)
    
    # Collect line ranges of self.parameters = {...} assignments to exclude
    excluded_lines = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and
            len(node.targets) == 1 and
            isinstance(node.targets[0], ast.Attribute) and
            node.targets[0].attr == "parameters"):
            # Exclude all lines in this assignment subtree
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    excluded_lines.add(child.lineno)
    
    count = 0
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and
            isinstance(node.value, (int, float)) and
            getattr(node, "lineno", None) not in excluded_lines):
            # Exclude common idiom constants
            if node.value not in (0, 1, -1, 2, True, False):
                count += 1
    return count
```

### Hard Gates

```python
def passes_hard_gates(metrics: WalkForwardResult) -> tuple[bool, str]:
    if metrics.avg_sharpe < 1.0:
        return False, f"Sharpe {metrics.avg_sharpe:.2f} < 1.0"
    if metrics.avg_maxdd > 0.30:
        return False, f"MaxDD {metrics.avg_maxdd:.1%} > 30%"
    if metrics.avg_trade_count < 50:
        return False, f"Trade count {metrics.avg_trade_count} < 50"
    if metrics.worst_fold_sharpe < 0:
        return False, f"Worst fold Sharpe {metrics.worst_fold_sharpe:.2f} < 0"
    return True, "OK"
```

### Oracle Hash Verification

```python
def compute_oracle_hash(oracle_dir: Path) -> str:
    """
    SHA256 of autodegen/oracle/ directory tree (sorted file paths + contents).
    Stored in every results.tsv row. Detects tampering even outside Docker.
    """
    hasher = hashlib.sha256()
    for path in sorted(oracle_dir.rglob("*.py")):
        hasher.update(path.read_bytes())
    return hasher.hexdigest()[:16]
```

### Regime Detection

```python
def classify_regime(data: pl.DataFrame, window: int = 90) -> str:
    """Rolling-window regime classifier. Upgradeable to HMM later."""
    closes = data["close"].tail(window).to_numpy()
    total_return = (closes[-1] / closes[0]) - 1
    threshold = 0.15
    
    if total_return > threshold:
        return "bull"
    elif total_return < -threshold:
        return "bear"
    else:
        return "crab"
```

---

## Strategy Interface

### v0: Single-Symbol `on_bar()`

```python
@dataclass
class Bar:
    timestamp: int          # Unix ms
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float           # base asset volume (BTC/ETH etc.), always >= 0 — ccxt default
    quote_volume: float = None  # optional: quote volume (USDT), populated if available
    funding_rate: float = None  # None for spot; forward-filled for perps
    # No bid_ask_spread — we don't collect L2 data at 30m+ timeframes

@dataclass
class Portfolio:
    cash: float
    positions: dict         # symbol -> {"size": float, "avg_price": float, "side": str}
    total_equity: float

@dataclass
class Signal:
    symbol: str
    side: Literal["buy", "sell", "close"]
    size: float             # base asset units (BTC/ETH etc.) — same units as bar.volume
    order_type: Literal["market", "limit"] = "market"
    limit_price: float = None

@dataclass
class Fill:
    signal: Signal
    fill_price: float
    fill_size: float
    fee: float
    timestamp: int
    pnl: float              # realized PnL for closes; 0 for opens
    is_close: bool          # True if this fill closes a position
    entry_value: float      # position value at entry (for return calculation)


class Strategy:
    name: str = "unnamed_strategy"
    parameters: dict = field(default_factory=dict)
    
    def __init__(self): pass
    
    def initialize(self, train_data: pl.DataFrame) -> None:
        """Optional: fit parameters to training data before walk-forward test phase."""
        pass
    
    def on_bar(self, bar: Bar, portfolio: Portfolio) -> list[Signal]:
        """Called on every bar. Return signals. Do NOT access external data."""
        raise NotImplementedError
    
    def on_fill(self, fill: Fill) -> None:
        """Called after order execution. Update internal state."""
        pass
```

### Phase 2 Upgrade Path: Multi-Symbol Interface

**Not implemented in Sprint 0-3.** Documented here so architecture supports it.

```python
# Phase 2: add to Strategy base class
def on_universe_bar(self, bars: dict[str, Bar], portfolio: Portfolio) -> list[Signal]:
    """
    Called with all symbols' bars at the same timestamp.
    For multi-asset strategies: cross-pair momentum, portfolio rebalancing.
    Returns signals across any symbol in bars.
    """
    pass
```

The single-symbol `on_bar()` remains primary for Sprint 0-3. `on_universe_bar()` is an additive extension — existing strategies don't break. The backtester calls whichever method the strategy implements.

### Example Strategy (Baseline)

```python
class MomentumCrossover(Strategy):
    """
    Dual EMA crossover on 1h/4h closes.
    Simple baseline. Expect it to fail. That's fine.
    """
    name = "momentum_ema_crossover_v1"
    parameters = {"fast_period": 20, "slow_period": 50, "position_size": 0.5}
    
    def __init__(self):
        self.fast_buffer = []
        self.slow_buffer = []
        self.in_position = False
    
    def _ema(self, buffer: list, period: int) -> float | None:
        if len(buffer) < period:
            return None
        k = 2 / (period + 1)
        ema = buffer[0]
        for price in buffer[1:]:
            ema = price * k + ema * (1 - k)
        return ema
    
    def on_bar(self, bar: Bar, portfolio: Portfolio) -> list[Signal]:
        self.fast_buffer.append(bar.close)
        self.slow_buffer.append(bar.close)
        self.fast_buffer = self.fast_buffer[-self.parameters["fast_period"]:]
        self.slow_buffer = self.slow_buffer[-self.parameters["slow_period"]:]
        
        fast_ema = self._ema(self.fast_buffer, self.parameters["fast_period"])
        slow_ema = self._ema(self.slow_buffer, self.parameters["slow_period"])
        
        if fast_ema is None or slow_ema is None:
            return []
        
        signals = []
        size = portfolio.total_equity * self.parameters["position_size"] / bar.close
        
        if fast_ema > slow_ema and not self.in_position:
            signals.append(Signal(symbol=bar.symbol, side="buy", size=size))
            self.in_position = True
        elif fast_ema < slow_ema and self.in_position:
            signals.append(Signal(symbol=bar.symbol, side="close", size=size))
            self.in_position = False
        
        return signals
```

---

## Agent Loop

### Config Parsing

At the start of every iteration, the agent reads `config.md`:

```python
def load_config(config_path: Path) -> AgentConfig:
    """
    Parse config.md. Log warning and use defaults on any parse failure.
    Never crash the loop over a malformed config file.
    """
    try:
        text = config_path.read_text()
        return parse_config_markdown(text)
    except Exception as e:
        logger.warning(f"config.md parse failed: {e} — using defaults")
        return AgentConfig()  # all defaults
```

### `program.md` (Agent Firmware)

The agent reads `program.md` at the start of each cycle. This file is human-authored, version-controlled.

```markdown
# autodegen program.md

## Objective
Discover strategies that pass walk-forward validation + validation holdout.
Composite score target: > 0.50. Elite: > 0.70.

## Current Best Score
best_composite: 0.41
best_strategy: momentum_ema_crossover_v3

## Research Directives
- Current focus: funding rate carry, mean reversion
- Avoid: HFT, orderbook microstructure, sub-30m bar strategies
- Complexity budget: 8 parameters max (enforced in score)
- Note: crab regime is underrepresented in wins. Explore ranging-market strategies.

## Constraints (DO NOT VIOLATE)
- Edit ONLY autodegen/sandbox/strategy.py
- Do not modify autodegen/oracle/ directory
- strategy.parameters must contain ALL tunable values
- No hardcoded numeric constants in method bodies outside self.parameters (AST lint enforces this)
- strategy.name must be unique per experiment
- Minimum bar timeframe: 30m. No tick data, no L2 orderbook features.

## Loop Protocol
1. Read config.md + program.md + strategy.py + last 10 lines results.tsv
2. State hypothesis in one sentence
3. Edit strategy.py
4. Run eval
5. Respond: hypothesis | score | outcome
```

### The Core Loop

```
LOOP:
  1. Read config.md (research directives, eval parameters)
  2. Read program.md + strategy.py + tail -n 10 results.tsv

  3. Agent proposes hypothesis (one sentence, logged before writing code)

  4. Agent edits autodegen/sandbox/strategy.py

  5. Validate commit:
     - Exactly one file changed: autodegen/sandbox/strategy.py
     - If anything else changed: abort, log error, continue
     git add autodegen/sandbox/strategy.py
     git commit -m "hypothesis: {hypothesis} | experiment_id: {id}"

  6. Run evaluation (SandboxRunner — Docker or systemd backend):
     try:
         result = runner.execute_strategy(
             strategy_path="autodegen/sandbox/strategy.py",
             data_path="data/",
             output_path="output/",
             timeout_seconds=900,
         )
         # Runner spawns: docker run --rm --read-only --network=none ...
         #                python -m autodegen.oracle.evaluate
             timeout=300,  # 5 min max per experiment
             capture_output=True,
         )
     except subprocess.TimeoutExpired:
         # kill subprocess, treat as error
         ...
     except Exception as e:
         # any failure: reset code, log error, continue
         git_reset_hard()
         log_error_to_results_tsv(hypothesis, error=str(e))
         continue

  7. Evaluation outputs:
     walk_forward_sharpe: 1.23
     max_drawdown: 18.4%
     composite_score: 0.57
     hard_gates: PASS
     regime_breakdown: bull=1.4, bear=0.9, crab=0.8

  8. Decision:
     IF composite_score > best_score AND hard_gates == PASS:
         best_score = composite_score
         update program.md
         # keep commit
     ELSE:
         git reset --hard HEAD~1  # discard code change (exactly one commit)
         # results.tsv entry already written (untracked), history preserved

  9. Append to results.tsv:
     {timestamp}\t{hypothesis}\t{composite_score}\t{sharpe}\t{maxdd}\t
     {trade_count}\t{kept}\t{strategy_hash}\t{oracle_hash}\t
     {data_snapshot_id}\t{config_hash}\t{status}

     status: "ok" | "error" | "timeout"

  10. GOTO 1
```

### Error Handling

The agent loop wraps the entire eval step in try/except:

```python
def run_experiment(agent_loop: AgentLoop, hypothesis: str) -> None:
    try:
        agent_loop.write_strategy(hypothesis)
        agent_loop.validate_and_commit()   # aborts if not exactly one file changed
        result = agent_loop.run_eval_with_timeout()
        agent_loop.decide_keep_or_reset(result)
        agent_loop.log_result(result, status="ok")
    
    except CommitValidationError as e:
        agent_loop.git_reset_if_uncommitted()
        agent_loop.log_result(None, status="error", error=str(e))
        # continue to next experiment
    
    except subprocess.TimeoutExpired:
        agent_loop.git_reset_hard()
        agent_loop.log_result(None, status="timeout")
    
    except Exception as e:
        logger.error(f"Experiment failed: {e}")
        agent_loop.git_reset_hard()
        agent_loop.log_result(None, status="error", error=str(e))
        # never rethrow — keep the loop alive
```

**No infinite restart loops.** One bad strategy = one discarded commit + one error row. The loop continues regardless.

### Git Commit Enforcement

Before every commit:

```python
def validate_and_commit(self, hypothesis: str, experiment_id: str) -> None:
    """
    Validate exactly one file changed before committing.
    Prevents agent from accidentally (or deliberately) modifying autodegen/oracle/.
    """
    changed = self._get_changed_files()  # git diff --name-only HEAD
    
    if len(changed) != 1:
        raise CommitValidationError(
            f"Expected exactly 1 changed file, got {len(changed)}: {changed}"
        )
    if changed[0] != "autodegen/sandbox/strategy.py":
        raise CommitValidationError(
            f"Changed file is {changed[0]}, expected autodegen/sandbox/strategy.py"
        )
    
    subprocess.run(
        ["git", "add", "autodegen/sandbox/strategy.py"],
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", f"hypothesis: {hypothesis} | id: {experiment_id}"],
        check=True,
    )
```

On `git reset --hard HEAD~1`: only reset if there's exactly one commit to reset (validate before resetting). Prevents cascading resets if something goes very wrong.

### Agent Prompt Template

```
SYSTEM: You are an autonomous quantitative strategy researcher.
Your job: write trading strategies that pass rigorous statistical validation.
You optimize for composite_score, not narrative quality.

FOCUS: 30m–4h crypto strategies (momentum, mean reversion, funding carry).
AVOID: HFT, sub-30m bars, orderbook microstructure, latency-dependent logic.

One strategy modification per iteration. State your hypothesis in one sentence.
Be surgical — change one thing at a time.

The oracle (autodegen/oracle/) is immutable and read-only. Edit only autodegen/sandbox/strategy.py.

[CONTEXT]
config.md: {contents}
program.md: {contents}
strategy.py: {contents}
last 10 experiments: {results.tsv tail}
[/CONTEXT]

State hypothesis and write the new strategy.py.
```

---

## Deployment Architecture: Docker + NixOS/systemd

### Core Principle

The autodegen codebase is **deployment-agnostic**. The sandbox runner is a thin abstraction layer with two backends:

- **Docker backend** (open source default) — universal compatibility, zero host assumptions
- **systemd backend** (NixOS production) — our own deployment, tighter integration, no Docker overhead

Pick one. They're interchangeable. The research loop doesn't care which is underneath.

### SandboxRunner Interface

Both backends implement this interface:

```python
class SandboxRunner:
    def execute_strategy(
        self,
        strategy_path: str,      # path to strategy.py
        data_path: str,           # path to backtest data
        output_path: str,         # where to write results
        timeout_seconds: int = 900,
        memory_limit_mb: int = 2048,
        cpu_cores: int = 2,
    ) -> ExecutionResult:
        ...
```

`ExecutionResult` carries stdout, stderr, exit code, wall time, and resource usage. The agent loop calls `runner.execute_strategy(...)` — it doesn't know or care whether Docker or systemd is underneath.

### Docker Backend (open source)

This is what goes in the README quickstart. Anyone with Docker installed can run this.

**Execution model**: The agent loop runs on the **host** (as a plain process or systemd service). It needs LLM API access and git access — running it inside Docker adds complexity with no benefit. The `docker-compose.yml` is for **data services only**.

**Data services** (`docker-compose.yml`):

```yaml
version: "3.9"

services:
  data-ingester:
    build: .
    command: uv run python -m autodegen.oracle.ingest --mode cron
    volumes:
      - ./data:/app/data                  # read-write: ingester writes parquet here
    environment:
      - BINANCE_API_KEY=${BINANCE_API_KEY}
      - BINANCE_SECRET=${BINANCE_SECRET}
    restart: unless-stopped

  paper-trader:
    build: .
    command: uv run python -m autodegen.paper_trader
    volumes:
      - ./data:/app/data:ro               # read-only: paper trader reads data
      - ./autodegen/sandbox:/app/autodegen/sandbox:ro  # read-only: reads strategy
    environment:
      - BINANCE_API_KEY=${BINANCE_API_KEY}
      - BINANCE_SECRET=${BINANCE_SECRET}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    profiles: ["paper"]
    restart: unless-stopped
```

**Agent loop runs on host** (start it directly):
```bash
# Run agent loop on host — it needs LLM API access and git access
ANTHROPIC_API_KEY=... uv run python -m autodegen.agent_loop
```

**Per-backtest isolation** via ephemeral container (spawned by `DockerSandboxRunner`):

```bash
docker run --rm \
  --read-only \
  --network=none \
  --memory=2g \
  --cpus=2 \
  --pids-limit=256 \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --user=nobody \
  -v ./autodegen/sandbox/strategy.py:/app/strategy.py:ro \
  -v ./data:/data:ro \
  -v ./output:/output:rw \
  autodegen-runner \
  uv run python -m autodegen.oracle.backtest --strategy /app/strategy.py
```

Hardening notes:
- `--read-only`: container filesystem is read-only; only explicitly mounted paths are writable
- `--network=none`: no network access during backtest — no calling home, no data exfil
- Oracle source is **baked into the image** — NOT mounted. The agent cannot reach oracle code even via mounts.
- `autodegen/sandbox/strategy.py` is mounted **read-only** — container can execute it, not modify it.
- `--cap-drop=ALL` + `--security-opt=no-new-privileges`: drops Linux capabilities, no privilege escalation
- Non-root user: belt-and-suspenders
- No `docker.sock` mount anywhere — that's root-equivalent host access, never needed

### systemd Backend (NixOS production)

This is how we run it on greencloud-vps. No Docker daemon. No container overhead. Tighter integration with the existing NixOS fleet managed via clan-private.

**Long-running services** defined as NixOS module:

```nix
# modules/autodegen.nix
{ config, lib, pkgs, ... }: {
  systemd.services.autodegen-data-ingester = { ... };
  systemd.services.autodegen-agent-loop = { ... };
  systemd.services.autodegen-paper-trader = { ... };
}
```

Each service gets standard systemd hardening in its `[Service]` section:
- `ProtectSystem=strict` / `ReadWritePaths=` only for what's needed
- `PrivateNetwork=yes` on **data-ingester** and **paper-trader** only — they use network but the service itself can be network-isolated for the process that doesn't (see per-backtest below)
- **Agent loop does NOT get `PrivateNetwork=yes`** — it needs LLM API access (Anthropic API) and potentially exchange data
- `PrivateTmp=yes`, `PrivateDevices=yes`
- `NoNewPrivileges=yes`
- `DynamicUser=yes` — transient UID per service, no persistent user to own

**Per-backtest isolation** via transient `systemd-run --scope`:

```bash
systemd-run --scope \
  --property=DynamicUser=yes \
  --property=ProtectSystem=strict \
  --property=ReadOnlyPaths=/ \
  --property=ReadWritePaths=/run/autodegen/output \
  --property=PrivateNetwork=yes \
  --property=PrivateTmp=yes \
  --property=PrivateDevices=yes \
  --property=NoNewPrivileges=yes \
  --property=MemoryMax=2G \
  --property=CPUQuota=200% \
  --property=TasksMax=256 \
  --property=RuntimeMaxSec=900 \
  uv run python -m autodegen.oracle.evaluate --strategy /path/to/strategy.py
```

Why this is better for our deployment:
- Zero Docker daemon overhead — no dockerd eating ~300MB base RAM
- systemd is already the process supervisor on NixOS; one less moving part
- Transient units get proper cgroup accounting visible in `journalctl` and `systemd-cgtop`
- Integrates with existing fleet monitoring (Prometheus node_exporter reads cgroup metrics)
- `DynamicUser=yes` provides strong user isolation without managing /etc/passwd entries

---

## Paper Trading Stage

### Architecture

```
[promotion trigger: walk-forward + validation holdout pass]
       │
       ▼
autodegen/paper_trader.py
  ├── subscribes to exchange websocket feed (1h bars)
  ├── calls strategy.on_bar() with real data
  ├── uses pending_signals queue — fills at NEXT bar's open (matches backtest model)
  └── records to paper_trades.db (SQLite)
```

### Websocket Integration

```python
import ccxt.pro as ccxtpro

async def run_paper_trader(strategy: Strategy, symbol: str, exchange_id: str):
    exchange = ccxtpro.binance()
    portfolio = Portfolio(initial_cash=10_000)
    pending_signals = []  # signals from previous bar, executed at current bar's open
    
    async for ohlcv in exchange.watch_ohlcv(symbol, "1h"):
        bar = Bar.from_ohlcv(ohlcv, symbol)
        
        # 1. Execute pending signals from PREVIOUS bar at this bar's open
        #    Matches backtest execution model: signal on bar N → fill at bar N+1 open.
        for signal in pending_signals:
            fill = simulate_fill(signal, fill_price=bar.open)
            portfolio.apply_fill(fill)
            strategy.on_fill(fill)
        pending_signals = []
        
        # 2. Strategy observes current bar and emits NEW signals
        new_signals = strategy.on_bar(bar, portfolio)
        pending_signals = new_signals  # queued for NEXT bar's open
        
        log_paper_trade_state(portfolio, bar)
```

### Promotion Gate

After 7 days of paper trading:

```python
def paper_trade_passes_gate(
    paper_result: PaperResult,
    backtest_result: BacktestResult,
    tolerance: float = 0.20
) -> tuple[bool, str]:
    sharpe_ratio = paper_result.sharpe / backtest_result.walk_forward_sharpe
    
    if sharpe_ratio < (1 - tolerance):
        return False, f"Paper Sharpe {paper_result.sharpe:.2f} is {1-sharpe_ratio:.0%} below backtest"
    
    if paper_result.max_drawdown > backtest_result.max_drawdown * 1.5:
        return False, f"Paper MaxDD {paper_result.max_drawdown:.1%} >> backtest MaxDD"
    
    return True, "Paper metrics within tolerance of backtest"
```

After paper gate passes: **human reviews test holdout results + strategy logic + equity curve** before any live deployment decision. No auto-deploy. Ever.

---

## Deployment on greencloud-vps

### Resource Budget

Base overhead depends on backend:

| Backend | Base overhead | RAM available for workloads |
|---|---|---|
| Docker | ~300MB (dockerd + images) | ~11.7GB |
| systemd (NixOS) | ~0MB | ~12GB (full) |

Per-strategy execution limits (both backends):

| Limit | Value |
|---|---|
| Memory per backtest | 2GB (`--memory=2g` / `MemoryMax=2G`) |
| CPU per backtest | 2 cores (`--cpus=2` / `CPUQuota=200%`) |
| Timeout per backtest | 15 min (`RuntimeMaxSec=900`) |

Full greencloud-vps budget (Docker backend, conservative):

| Component | CPU | RAM | Disk |
|---|---|---|---|
| Data ingester (idle) | < 0.1 vCPU | 200MB | 50GB parquet |
| Agent loop (active) | 2 vCPU | 4GB | — |
| Paper trader | 0.5 vCPU | 500MB | 1GB |
| SQLite state | — | 100MB | 2GB |
| OS + Docker overhead | 0.5 vCPU | 2GB | 20GB |
| **Available for 2nd agent** | **~2 vCPU** | **~5.2GB** | — |

With systemd backend: skip the ~300MB Docker base overhead, 2-3 parallel agents are comfortably within 12GB. At steady state: 2-3 parallel agent scopes feasible under either backend.

### Data Retention

- OHLCV parquet: keep all (~200MB/year per pair per resolution)
- results.tsv: keep all (text, tiny)
- Paper trade logs: last 30 days of completed runs
- Agent conversation logs: last 100 experiments

---

## Domain Plugin Architecture

> **First-class design principle.** The oracle/sandbox pattern is domain-agnostic. Crypto spot/perps is the first research track, not the only one.

### Core Insight

The research loop infrastructure is shared across all domains:

```
shared infrastructure (never domain-specific):
├── autodegen/agent_loop.py          — reads config, calls LLM, manages git
├── autodegen/sandbox/runner.py      — SandboxRunner (Docker + systemd)
├── config.md + program.md           — human steering
├── results.tsv                      — experiment ledger
└── git commit/reset mechanics       — version control as experiment ledger

domain-specific (one module per track):
└── autodegen/oracle/<domain>/
    ├── ingest.py       — domain data pipeline
    ├── backtest.py     — domain-specific simulation (or evaluator)
    └── evaluate.py     — domain-specific metrics (Sharpe, EV, ROI, etc.)
```

Adding a new research track = adding a new oracle module + extending the strategy interface. **You do not rewrite the agent loop, git ledger, or config system.**

### Planned Research Tracks

| Track | Oracle Module | Eval Metric | Sprint |
|---|---|---|---|
| Crypto spot/perps (30m–4h) | `oracle.crypto` | Trade-return Sharpe | Sprint 0–5 |
| Prediction markets (Polymarket, Kalshi) | `oracle.prediction` | Expected Value vs outcome | Sprint 7 |
| DeFi portfolio optimization (LP, yield farming, IL) | `oracle.defi` | Risk-adjusted yield | Future |
| MEV extraction simulation (sandwich, arb, liquidation) | `oracle.mev` | Net ETH captured | Future |
| Funding rate carry (cross-venue) | `oracle.funding_carry` | Carry Sharpe | Future |
| Options/structured products | `oracle.options` | P&L vs benchmark | Future |
| Macro regime allocation (BTC/ETH/stables/DeFi rotation) | `oracle.macro` | Portfolio Sharpe | Future |

### Domain Contract

Each research track implements:

```python
# Each domain oracle provides these:
class DomainOracle(Protocol):
    def ingest(self, config: DomainConfig) -> None:
        """Fetch and store domain data."""
        ...
    
    def backtest(self, strategy: Strategy, data: Any, config: BacktestConfig) -> BacktestResult:
        """Domain-specific simulation."""
        ...
    
    def evaluate(self, result: BacktestResult) -> Metrics:
        """Domain-specific metrics. Sharpe for trading, EV for prediction markets, etc."""
        ...

# Each domain extends the base strategy interface:
class CryptoStrategy(Strategy):
    def on_bar(self, bar: CryptoBar, portfolio: Portfolio) -> list[Signal]: ...

class PredictionStrategy(Strategy):
    def on_market_update(self, market: PredictionMarket, portfolio: Portfolio) -> list[Bet]: ...
```

The `SandboxRunner` always executes `python -m autodegen.oracle.<domain>.evaluate` — the domain name comes from `config.md`. The agent loop never has domain-specific logic; it just reads the composite score out of the result.

### Sprint 0 Scope

Sprint 0 implements the `crypto` domain with the flat `autodegen/oracle/` layout (single domain for now). The multi-domain namespace (`oracle.crypto`, `oracle.prediction`, etc.) is introduced when Sprint 7 adds the second track. Refactoring from flat to namespaced is a clean import path change — the oracle implementations don't change.

---

## Sprint Plan

### Sprint 0: Foundation (Data Pipeline + Sandbox Runner)
**Goal**: Fetch and store BTC/USDT 1h OHLCV from Binance. Scaffold the sandbox runner abstraction. Single pair, single exchange.

Deliverables:
- `autodegen/oracle/ingest.py`: ccxt fetch, parquet storage, incremental updates, data quality gate
- `data/`: directory structure, gitignored
- `pyproject.toml`: uv project with ccxt, polars, pyarrow deps
- `scripts/backfill.py`: one-shot 2-year historical backfill
- `config.md`: initial version with defaults (includes `sandbox_backend: docker`)
- `autodegen/sandbox/runner.py`: `SandboxRunner` abstract base + `DockerSandboxRunner` + `SystemdSandboxRunner` stubs
- `docker-compose.yml`: data-ingester and paper-trader services — open source onboarding entrypoint (agent-loop runs on host)
- `Dockerfile`: backtester image with oracle baked in, no oracle dir mount needed

Done when: `uv run python -m autodegen.oracle.ingest` fetches last 30 days of BTC/USDT 1h without errors. Data quality gate rejects a synthetic bad-data file. `DockerSandboxRunner.execute_strategy()` runs a no-op strategy in a hardened ephemeral container and returns an `ExecutionResult`.

### Sprint 1: Backtester Core
**Goal**: Event-driven backtester with correct fill timing for 30m+ bars.

Deliverables:
- `autodegen/oracle/backtest.py`: event loop, pending_signals queue (one-bar delay), Bar/Portfolio/Signal/Fill dataclasses, slippage model (base volume units), fee model
- `autodegen/sandbox/strategy.py`: MomentumCrossover baseline
- Unit tests: look-ahead bias check, fill timing (next-bar open via pending_signals), slippage calculation (base units)
- Funding payment simulation (8h intervals, forward-filled rates)

Done when: Baseline strategy produces reproducible results on 1h bars. Same data + same code = same output. Always.

### Sprint 2: Eval Pipeline
**Goal**: Walk-forward validation (expanding window), composite score, hard gates, three-way split.

Deliverables:
- `autodegen/oracle/evaluate.py`: expanding-window walk-forward, trade-return Sharpe, composite score with AST lint (excludes self.parameters dict), regime detection
- Three-way data split: walk-forward (~70%) / validation holdout (15%) / test holdout (15%) — agent never touches test holdout
- `results.tsv`: schema defined with all hash columns (strategy_hash, oracle_hash, data_snapshot_id, config_hash)
- CLI: `uv run python -m autodegen.oracle.evaluate --strategy autodegen/sandbox/strategy.py`

Done when: Eval produces correct scores. Correctly rejects a strategy that predicts the future. Walk-forward uses expanding windows. Sharpe is computed from trade returns.

### Sprint 3: Agent Loop
**Goal**: LLM agent runs the full loop autonomously.

Deliverables:
- `program.md`: initial firmware
- `autodegen/agent_loop.py`: reads config.md + context, calls LLM, writes strategy, runs eval (with timeout), git commit/reset, error handling
- Config parser: reads config.md each iteration, uses defaults on parse failure
- Git enforcement: validates exactly one file changed before commit
- Integration test: agent runs 5 experiments without human intervention, handles a malformed strategy gracefully

Done when: Agent runs overnight, generates 20+ experiments, results.tsv has meaningful variance, no crashes on bad strategies.

### Sprint 4: Multi-Pair + Funding Rates + NixOS Deployment
**Goal**: Expand data pipeline to 5 pairs + funding rate data. Deploy to greencloud-vps via NixOS module.

Deliverables:
- Ingest: ETH, SOL, BNB, XRP perps OHLCV (30m, 1h, 4h)
- Ingest: Funding rate history for all 5 pairs, forward-filled
- Backtester: funding payment simulation verified
- `modules/autodegen.nix`: NixOS module for greencloud-vps — defines agent-loop, data-ingester, paper-trader systemd services with full hardening directives
- `SystemdSandboxRunner`: complete implementation (was stub in Sprint 0) — `systemd-run --scope` with all hardening properties wired up
- NixOS module added to clan-private, deployed to greencloud-vps
- Config: `sandbox_backend: systemd` in production config

Done when: Funding rate carry baseline strategy runs with funding payments in PnL attribution. Full agent loop running on greencloud-vps under systemd, no Docker daemon installed.

### Sprint 5: Paper Trader
**Goal**: Promote best strategy to live paper trading.

Deliverables:
- `autodegen/paper_trader.py`: 1h websocket feed, fill simulation at next-bar open, paper_trades.db
- Promotion gate: 7-day paper run + metrics comparison vs backtest
- Monitoring: daily summary of paper vs backtest metrics

Done when: Strategy runs 7 days in paper mode, generates promotion gate report.

### Sprint 6: Agent Swarm
**Goal**: 3 agents in parallel exploring different strategy families.

Deliverables:
- Agent isolation: each agent on its own branch, separate config.md directives
- Coordinator: reviews branches, selects candidates for promotion
- Resource management: 4g mem_limit per agent, verified under load

Done when: 3 agents run simultaneously without OOM on greencloud-vps.

### Sprint 7: Prediction Markets
**Goal**: Polymarket data pipeline + first prediction market strategy (1h+ timeframe).

Deliverables:
- Ingest: Polymarket CLOB API, hourly odds history
- Eval: EV-based scorer for binary outcomes
- Strategy: first prediction market edge strategy

Done when: ≥1 prediction market strategy passes walk-forward validation.

---

## Critical Design Decisions

### Why custom backtester?

Because in every backtesting framework, there are magic moments — places where the code does something you didn't expect and your Sharpe 3.0 backtest is benefiting from a look-ahead bug six layers deep. With 500 lines of clean, auditable Python, there are no surprises. The custom backtester is a feature.

### Why 30m minimum bar?

Because our edge is research breadth, not execution speed. Sub-minute strategies require co-location, microstructure data, and nanosecond fill simulation — a completely different stack. At 30m bars, a linear participation-rate slippage model is accurate. Signal quality matters more than latency. This is the right constraint for the infrastructure we have.

### Why trade-return Sharpe instead of equity-curve Sharpe?

Because equity-curve bar returns have autocorrelation from open positions. A slow-moving strategy holding a winning position produces a smooth "drift" in equity that inflates the apparent Sharpe dramatically. Trade returns are independent samples with no such artifact. Trade-return Sharpe is the honest number.

### Why the three-way data split?

Because an agent running 200 experiments against a fixed validation holdout will eventually find strategies that happen to fit that specific period. The test holdout — never touched by the agent, only reviewed by a human — is the final safeguard. If even the test holdout looks good after human review, you have a real strategy.

### Why config.md over YAML/environment variables?

Because humans edit it, agents read it, it's self-documenting, and it's version-controlled alongside experiment history. A YAML parse error in a subprocess silently produces wrong behavior; a malformed markdown section is just plain text with a warning in the log. Human-readable first.

### Why no docker.sock?

Docker socket gives a container root-equivalent access to the host. The agent container doesn't need it. Git runs as a normal subprocess. Removing docker.sock is free security hardening.

### Why two deployment backends instead of just Docker everywhere?

Because Docker is a great open source default but a bad production choice on a constrained VPS we already own. A running dockerd eats ~300MB of RAM before you've started a single container. On a 12GB VPS that's not catastrophic, but it's also entirely avoidable. More importantly, we already manage greencloud-vps with NixOS/systemd via clan-private — adding Docker as a second process supervisor creates two sources of truth for service lifecycle. The systemd backend fits cleanly into what we already have. The `SandboxRunner` abstraction means we never have to choose: Docker in the repo for everyone else, systemd in production for us.

### Why enforce simplicity in the score formula, not just the prompt?

Because LLMs are excellent at rationalizing complexity. If simplicity is only in the instructions, the agent writes a 12-parameter strategy and explains beautifully why each parameter is necessary. If it's in the score formula + the AST lint catches hardcoded constants, the agent is directly optimizing against complexity. Math beats prose.

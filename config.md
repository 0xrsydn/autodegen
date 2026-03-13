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
- validation_pct: 15%
- test_pct: 15%

## Research Directives
- focus: momentum, mean reversion
- avoid: HFT, orderbook microstructure
- complexity_budget: 8 parameters max

## Deployment
- sandbox_backend: docker

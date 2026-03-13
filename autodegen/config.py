from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AppConfig:
    pairs: list[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    exchanges: list[str] = field(default_factory=lambda: ["binance"])
    timeframes: list[str] = field(default_factory=lambda: ["1h", "4h"])
    max_leverage: float = 3.0
    max_position_pct: float = 25.0
    max_drawdown_tolerance: float = 20.0
    min_sharpe: float = 1.0
    min_trades: int = 50
    walk_forward_folds: int = 8
    validation_pct: float = 15.0
    test_pct: float = 15.0
    focus: list[str] = field(default_factory=lambda: ["momentum", "mean reversion"])
    avoid: list[str] = field(default_factory=lambda: ["HFT", "orderbook microstructure"])
    complexity_budget: str = "8 parameters max"
    sandbox_backend: str = "docker"


DEFAULT_CONFIG_MD = """# autodegen config

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
"""


def _parse_list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_num(value: str) -> float:
    value = value.strip().lower().removesuffix("x").removesuffix("%")
    return float(value)


def parse_config_markdown(text: str) -> AppConfig:
    cfg = AppConfig()
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("-") or ":" not in line:
            continue
        key, value = line[1:].split(":", 1)
        k = key.strip()
        v = value.strip()
        try:
            if k == "pairs":
                cfg.pairs = _parse_list(v)
            elif k == "exchanges":
                cfg.exchanges = _parse_list(v)
            elif k == "timeframes":
                cfg.timeframes = _parse_list(v)
            elif k == "max_leverage":
                cfg.max_leverage = _parse_num(v)
            elif k == "max_position_pct":
                cfg.max_position_pct = _parse_num(v)
            elif k == "max_drawdown_tolerance":
                cfg.max_drawdown_tolerance = _parse_num(v)
            elif k == "min_sharpe":
                cfg.min_sharpe = float(v)
            elif k == "min_trades":
                cfg.min_trades = int(v)
            elif k == "walk_forward_folds":
                cfg.walk_forward_folds = int(v)
            elif k == "validation_pct":
                cfg.validation_pct = _parse_num(v)
            elif k == "test_pct":
                cfg.test_pct = _parse_num(v)
            elif k == "focus":
                cfg.focus = _parse_list(v)
            elif k == "avoid":
                cfg.avoid = _parse_list(v)
            elif k == "complexity_budget":
                cfg.complexity_budget = v
            elif k == "sandbox_backend":
                cfg.sandbox_backend = v
        except Exception as exc:
            logger.warning("Invalid config value for %s=%s (%s); using default", k, v, exc)
    return cfg


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        logger.warning("config file missing at %s, using defaults", path)
        return AppConfig()
    try:
        return parse_config_markdown(path.read_text())
    except Exception as exc:
        logger.warning("malformed config file at %s (%s), using defaults", path, exc)
        return AppConfig()


def config_hash(path: Path) -> str:
    if not path.exists():
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_default_config(path: Path) -> None:
    path.write_text(DEFAULT_CONFIG_MD)

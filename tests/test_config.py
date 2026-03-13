from __future__ import annotations

from pathlib import Path

from autodegen.config import AppConfig, config_hash, load_config, parse_config_markdown, write_default_config


def test_parse_valid_config() -> None:
    text = """# autodegen config
## Trading Universe
- pairs: BTC/USDT, ETH/USDT
- exchanges: binance
- timeframes: 1h, 4h
## Risk Constraints
- max_leverage: 4x
- max_position_pct: 20%
"""
    cfg = parse_config_markdown(text)
    assert cfg.pairs == ["BTC/USDT", "ETH/USDT"]
    assert cfg.max_leverage == 4.0
    assert cfg.max_position_pct == 20.0


def test_parse_missing_sections_uses_defaults() -> None:
    cfg = parse_config_markdown("# empty")
    assert cfg == AppConfig()


def test_parse_malformed_markdown_uses_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.md"
    path.write_text("\x00\x00\x00")
    cfg = load_config(path)
    assert isinstance(cfg, AppConfig)


def test_default_types_and_values() -> None:
    cfg = AppConfig()
    assert isinstance(cfg.min_trades, int)
    assert isinstance(cfg.min_sharpe, float)
    assert cfg.walk_forward_folds == 8
    assert cfg.sandbox_backend == "docker"


def test_config_hash_computation(tmp_path: Path) -> None:
    path = tmp_path / "config.md"
    write_default_config(path)
    h1 = config_hash(path)
    path.write_text(path.read_text() + "\n- min_trades: 10\n")
    h2 = config_hash(path)
    assert h1 != h2

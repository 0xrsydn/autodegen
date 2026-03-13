from prepare import evaluate, synthetic_bars
from strategy import Strategy


def test_strategy_interface():
    s = Strategy()
    assert hasattr(Strategy, "name")
    assert hasattr(Strategy, "parameters")
    assert callable(getattr(s, "on_bar"))
    assert callable(getattr(s, "initialize"))


def test_ema_crossover_produces_signal():
    s = Strategy()
    s.initialize([])
    bars = synthetic_bars(80)
    saw = False
    for b in bars:
        signals = s.on_bar(b, {"cash": 10000, "position": 0, "equity": 10000})
        if signals:
            saw = True
            break
    assert saw


def test_importable_instantiable_and_end_to_end_eval():
    s = Strategy()
    assert s is not None
    out = evaluate(Strategy, synthetic_bars(300))
    assert "avg_sharpe" in out

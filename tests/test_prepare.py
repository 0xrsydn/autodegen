from datetime import UTC, datetime, timedelta

from prepare import Bar, bars_per_year, target_test_bars, target_train_bars, walk_forward_splits


def _make_bars(n: int, timeframe: str = "1h") -> list[Bar]:
    step_seconds = {"1h": 3600, "15m": 900}[timeframe]
    ts = datetime(2020, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for i in range(n):
        price = 10000.0 + i
        bars.append(Bar(ts, price, price + 10, price - 10, price + 1, 1000.0))
        ts += timedelta(seconds=step_seconds)
    return bars


def test_walk_forward_splits_span_full_wf_segment() -> None:
    wf_bars = _make_bars(46_000, timeframe="1h")
    splits = walk_forward_splits(wf_bars, n_folds=6, timeframe="1h")

    assert len(splits) == 6

    train_size = target_train_bars("1h")
    test_size = target_test_bars("1h")
    available = len(wf_bars) - train_size
    step = max(test_size, (available - test_size) // (6 - 1))

    first_test_start_idx = len(splits[0][0])
    last_test_end_idx = len(splits[-1][0]) + len(splits[-1][1])

    assert first_test_start_idx == train_size
    assert 4300 <= first_test_start_idx <= 4340
    assert last_test_end_idx >= len(wf_bars) - step
    assert last_test_end_idx > 10_800


def test_bars_per_year_varies_by_timeframe() -> None:
    assert bars_per_year("1h") == 8766
    assert bars_per_year("15m") == 35064
    assert bars_per_year("30m") == 17532

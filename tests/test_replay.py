"""Unit tests for the point-in-time replay slicer — the no-look-ahead guardrail.

Hand-built frames on an explicit integer time grid (no network). Asserts that a slice
at a timestamp contains only bars closed at or before it, that the reference-timeframe
close sequence is correct, and — crucially — that a future spike appended after the
timestamp provably does not change the sliced frames.
"""

from __future__ import annotations

import pandas as pd

from trader.market_data import Candles
from trader.replay import Replay, timeframe_to_ms

_HOUR = 3_600_000


def _frame(symbol: str, timeframe: str, step_ms: int, rows: int, *, high_at: int | None = None,
           spike: float = 0.0) -> Candles:
    """A simple ascending frame; optionally inject a ``spike`` high at bar ``high_at``."""
    data = []
    for i in range(rows):
        close = 100.0 + i
        high = close + 1.0
        if high_at is not None and i == high_at:
            high = close + spike
        data.append([i * step_ms, close, high, close - 1.0, close, 1_000.0 + i])
    frame = pd.DataFrame(
        data, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


def test_timeframe_to_ms_parses_units() -> None:
    assert timeframe_to_ms("15m") == 15 * 60_000
    assert timeframe_to_ms("1h") == _HOUR
    assert timeframe_to_ms("4h") == 4 * _HOUR
    assert timeframe_to_ms("1d") == 86_400_000


def test_slice_keeps_only_bars_closed_at_or_before_ts() -> None:
    # 1h bars opening at 0,1h,2h,... close at 1h,2h,3h,...; slice at the close of bar 2.
    frame = _frame("AAA", "1h", _HOUR, rows=6)
    replay = Replay({"1h": frame}, reference_timeframe="1h")

    ts = 3 * _HOUR  # close time of the bar that opened at 2h
    sliced = replay.slice_at(ts)["1h"].frame

    # Bars 0,1,2 have closed (close times 1h,2h,3h all <= ts); bars 3,4,5 have not.
    assert list(sliced["timestamp"]) == [0, _HOUR, 2 * _HOUR]


def test_reference_closes_are_open_plus_one_duration() -> None:
    frame = _frame("AAA", "1h", _HOUR, rows=4)
    replay = Replay({"1h": frame}, reference_timeframe="1h")
    assert replay.reference_closes() == (_HOUR, 2 * _HOUR, 3 * _HOUR, 4 * _HOUR)


def test_multi_timeframe_slice_respects_each_timeframes_close() -> None:
    # 1h finer + 4h reference on the same grid. Slice at the close of the first 4h bar.
    finer = _frame("AAA", "1h", _HOUR, rows=12)
    reference = _frame("AAA", "4h", 4 * _HOUR, rows=3)
    replay = Replay({"1h": finer, "4h": reference}, reference_timeframe="4h")

    ts = 4 * _HOUR  # first 4h bar's close
    sliced = replay.slice_at(ts)

    # The 4h frame keeps just its first bar; the 1h frame keeps the four bars closed by 4h.
    assert list(sliced["4h"].frame["timestamp"]) == [0]
    assert list(sliced["1h"].frame["timestamp"]) == [0, _HOUR, 2 * _HOUR, 3 * _HOUR]


def test_future_spike_after_ts_does_not_change_the_slice() -> None:
    # Explicit no-look-ahead proof: a huge spike placed on a bar AFTER ts must not
    # appear in — or otherwise alter — the slice taken at ts.
    baseline = _frame("AAA", "1h", _HOUR, rows=8)
    with_spike = _frame("AAA", "1h", _HOUR, rows=8, high_at=6, spike=10_000.0)

    ts = 4 * _HOUR  # bars 0..3 have closed; the spike is on bar 6 (far in the future)
    sliced_baseline = Replay({"1h": baseline}, "1h").slice_at(ts)["1h"].frame
    sliced_spiked = Replay({"1h": with_spike}, "1h").slice_at(ts)["1h"].frame

    # Identical slices: the future spike is invisible at ts.
    assert sliced_baseline.equals(sliced_spiked)
    assert sliced_spiked["high"].max() < 10_000.0


def test_max_bars_caps_slice_to_last_k_bars_closed_before_ts() -> None:
    # 20 hourly bars; slice at the close of bar 14 keeping only the last 5 closed bars.
    frame = _frame("AAA", "1h", _HOUR, rows=20)
    replay = Replay({"1h": frame}, reference_timeframe="1h", max_bars=5)

    ts = 15 * _HOUR  # bars 0..14 have closed (close times 1h..15h)
    sliced = replay.slice_at(ts)["1h"].frame

    # Capped at 5 rows, and they are the MOST-RECENT five closed bars (10..14), not the
    # oldest — the window is a fixed-size trailing window.
    assert len(sliced) == 5
    assert list(sliced["timestamp"]) == [
        10 * _HOUR,
        11 * _HOUR,
        12 * _HOUR,
        13 * _HOUR,
        14 * _HOUR,
    ]
    # No look-ahead: bar 15 (which closes at 16h, after ts) is excluded even though the
    # tail is only 5 wide.
    assert (15 * _HOUR) not in list(sliced["timestamp"])


def test_max_bars_none_returns_all_bars_closed_before_ts() -> None:
    # The default (max_bars=None) keeps every closed bar — unchanged behaviour.
    frame = _frame("AAA", "1h", _HOUR, rows=20)
    replay = Replay({"1h": frame}, reference_timeframe="1h")

    ts = 15 * _HOUR
    sliced = replay.slice_at(ts)["1h"].frame

    assert len(sliced) == 15
    assert list(sliced["timestamp"]) == [i * _HOUR for i in range(15)]


def test_max_bars_applies_per_timeframe() -> None:
    # Each timeframe is independently capped to its own last max_bars closed bars.
    finer = _frame("AAA", "1h", _HOUR, rows=40)
    reference = _frame("AAA", "4h", 4 * _HOUR, rows=10)
    replay = Replay({"1h": finer, "4h": reference}, reference_timeframe="4h", max_bars=3)

    ts = 40 * _HOUR  # all 40 finer bars and all 10 reference bars have closed by now
    sliced = replay.slice_at(ts)

    assert len(sliced["1h"].frame) == 3
    assert len(sliced["4h"].frame) == 3
    # The kept finer bars are the last three closed hourly bars.
    assert list(sliced["1h"].frame["timestamp"]) == [37 * _HOUR, 38 * _HOUR, 39 * _HOUR]

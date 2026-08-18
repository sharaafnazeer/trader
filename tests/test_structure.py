"""Unit tests for market-structure detection (pure, no network).

Hand-built OHLCV frames with known swing points drive the detector; assertions pin
the externally-observable outcomes (the BULLISH/BEARISH classification and the exact
swing pivot levels) rather than internal mechanics.
"""

from __future__ import annotations

import pandas as pd

from trader.direction import Direction, _timeframe_direction
from trader.indicators import TimeframeFeatures
from trader.market_data import Candles
from trader.structure import Structure, analyze

# A zigzag whose highs peak at 10, 12, 14 (higher highs) and whose lows trough at
# 5, 7, 9 (higher lows). Each extremum is a strict local max/min over a 2-candle
# window, so detection is deterministic.
_BULLISH_PATH = [6.0, 8.0, 10.0, 8.0, 5.0, 8.5, 12.0, 8.5, 7.0, 10.5, 14.0, 11.0, 9.0, 10.0, 11.0]


def _candles_from_path(path: list[float]) -> Candles:
    """Build candles whose high/low straddle each path value by a fixed band."""
    rows = []
    for i, value in enumerate(path):
        rows.append([i, value, value + 0.5, value - 0.5, value, 1_000.0])
    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return Candles(symbol="TEST/USDT", timeframe="4h", frame=frame)


def test_higher_highs_and_higher_lows_yield_bullish_with_matching_pivots() -> None:
    state = analyze(_candles_from_path(_BULLISH_PATH))

    assert state.structure is Structure.BULLISH
    # Swing highs are the path peaks + 0.5 band; swing lows the troughs - 0.5 band.
    assert state.swing_highs == (10.5, 12.5, 14.5)
    assert state.swing_lows == (4.5, 6.5, 8.5)
    assert state.last_swing_high == 14.5
    assert state.last_swing_low == 8.5


def test_lower_highs_and_lower_lows_yield_bearish_with_matching_pivots() -> None:
    # Reversing a zigzag keeps the strict extrema but descends: highs 14,12,10 and
    # lows 9,7,5 -> lower highs and lower lows.
    state = analyze(_candles_from_path(list(reversed(_BULLISH_PATH))))

    assert state.structure is Structure.BEARISH
    assert state.swing_highs == (14.5, 12.5, 10.5)
    assert state.swing_lows == (8.5, 6.5, 4.5)


def test_mixed_structure_is_broken() -> None:
    # Higher high but lower low (an expanding range) is neither trend -> BROKEN.
    # Peaks 10, 14 (higher high) with troughs 7, 5 (lower low).
    path = [6.0, 8.0, 10.0, 8.0, 7.0, 9.0, 14.0, 9.0, 5.0, 8.0, 9.0]
    state = analyze(_candles_from_path(path))

    assert state.structure is Structure.BROKEN


def test_insufficient_history_is_broken_with_no_pivots() -> None:
    state = analyze(_candles_from_path([1.0, 2.0, 3.0]))

    assert state.structure is Structure.BROKEN
    assert state.last_swing_high is None
    assert state.last_swing_low is None


def _bullish_stack_features() -> TimeframeFeatures:
    """Features with a clean bullish EMA 20/50/200 stack (the stack drives direction)."""
    return TimeframeFeatures(
        symbol="X",
        timeframe="1d",
        ema20=3.0,
        ema50=2.0,
        ema200=1.0,
        rsi=55.0,
        macd=0.0,
        macd_signal=0.0,
        macd_hist=0.0,
        roc=0.0,
        atr=1.0,
        obv=0.0,
        obv_slope=0.0,
        bollinger_width=0.0,
        relative_volume=1.0,
    )


def test_too_few_swings_yields_insufficient_state_that_does_not_oppose_direction() -> None:
    # A frame with fewer than the minimum swings is the insufficient state (BROKEN).
    state = analyze(_candles_from_path([1.0, 2.0, 3.0]))
    assert state.structure is Structure.BROKEN

    # And that insufficient state does not veto a clean stack: the EMA-driven direction
    # still resolves (structure only vetoes when it clearly opposes), so a bullish stack
    # over insufficient structure is LONG rather than NONE.
    assert _timeframe_direction(_bullish_stack_features(), state) is Direction.LONG

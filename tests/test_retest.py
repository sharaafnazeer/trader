"""Tests for breakout-as-a-sequence: the detector, its wiring, and its evidence.

The detector is the densest thing in this feature, so it is tested the way a classifier
should be — hand-built sequences with an exact expected state, one per outcome, and each
long sequence mirrored around the level to give the short-side equivalent for free. A
mirrored assertion is worth more than a second hand-written sequence: it cannot drift out
of agreement with the long-side case it is supposed to reflect.

Beyond classification, two things matter. A **failed** retest must earn no breakout credit
— that is the whole point, a level broken and then lost is evidence against the trade, not
a weaker breakout. And the flag must reach **both** the live path and the backtest path;
wire only one and either the measurement describes something the scan never did, or the
scan does something that was never measured.
"""

from __future__ import annotations

import pandas as pd
import pytest

from trader.direction import Direction
from trader.indicators import TimeframeFeatures
from trader.market_data import Candles
from trader.retest import RetestState, detect, detect_by_timeframe, level_for
from trader.structure import Structure, StructureState

_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

# The level under test and the ATR the tolerance is expressed in. With a tolerance of
# 0.25 ATR the band is 100 ± 1.0, so every number below reads directly: 102 is decisively
# beyond it, 100.5 is inside it, 98 is decisively under it.
LEVEL = 100.0
ATR = 4.0
TOLERANCE_ATR = 0.25
BAND = ATR * TOLERANCE_ATR  # 1.0

Bar = tuple[float, float, float]  # (high, low, close)


def _candles(bars: list[Bar], symbol: str = "AAA", timeframe: str = "4h") -> Candles:
    """A frame from (high, low, close) triples; the open is unused by the detector."""

    rows = [
        [i * 14_400_000, close, high, low, close, 1_000.0]
        for i, (high, low, close) in enumerate(bars)
    ]
    return Candles(symbol=symbol, timeframe=timeframe, frame=pd.DataFrame(rows, columns=_COLUMNS))


def _mirror(bars: list[Bar]) -> list[Bar]:
    """Reflect a sequence around the level, turning a long setup into its short twin."""

    return [(2 * LEVEL - low, 2 * LEVEL - high, 2 * LEVEL - close) for high, low, close in bars]


def _classify(bars: list[Bar], direction: Direction, lookback: int = 12) -> RetestState:
    return detect(
        _candles(bars),
        LEVEL,
        direction,
        ATR,
        lookback=lookback,
        tolerance_atr=TOLERANCE_ATR,
    )


# The four sequences, written from the long side. Each is 12 bars so a lookback of 12 sees
# all of it. The band is [99, 101]: a close above 101 breaks, a low at or below 101 returns,
# a close below 99 loses the level.
_BELOW = [(98.0, 94.0, 96.0)] * 4

NO_BREAK: list[Bar] = _BELOW + [
    (100.6, 95.0, 97.0),
    (100.9, 96.0, 98.0),
    (100.4, 95.5, 97.5),
    (100.8, 96.5, 98.5),
    (100.2, 95.0, 97.0),
    (100.7, 96.0, 98.0),
    (100.5, 95.5, 97.5),
    (100.9, 96.0, 98.9),
]

BROKEN: list[Bar] = _BELOW + [
    (104.0, 99.5, 103.0),  # the break: a close decisively above the band
    (106.0, 102.5, 105.0),
    (108.0, 104.5, 107.0),
    (110.0, 106.5, 109.0),
    (112.0, 108.5, 111.0),
    (114.0, 110.5, 113.0),
    (116.0, 112.5, 115.0),
    (118.0, 114.5, 117.0),
]

HELD: list[Bar] = _BELOW + [
    (104.0, 99.5, 103.0),  # break
    (106.0, 102.5, 105.0),
    (105.5, 100.5, 102.0),  # return into the band
    (104.0, 101.5, 103.5),  # holds
    (106.0, 103.0, 105.0),  # and advances
    (108.0, 105.0, 107.0),
    (110.0, 107.0, 109.0),
    (112.0, 109.0, 111.0),
]

FAILED: list[Bar] = _BELOW + [
    (104.0, 99.5, 103.0),  # break
    (106.0, 102.5, 105.0),
    (105.5, 100.5, 102.0),  # return into the band
    (103.0, 97.0, 98.0),  # and loses it on a close
    (99.0, 94.0, 95.0),
    (97.0, 92.0, 93.0),
    (95.0, 90.0, 91.0),
    (93.0, 88.0, 89.0),
]

_EXPECTED = [
    (NO_BREAK, RetestState.NONE),
    (BROKEN, RetestState.BROKEN_NO_RETEST),
    (HELD, RetestState.RETEST_HELD),
    (FAILED, RetestState.RETEST_FAILED),
]


# --- The four states, long and mirrored short -------------------------------------


@pytest.mark.parametrize(("bars", "expected"), _EXPECTED)
def test_each_sequence_is_classified_for_a_long(bars: list[Bar], expected: RetestState) -> None:
    assert _classify(bars, Direction.LONG) is expected


@pytest.mark.parametrize(("bars", "expected"), _EXPECTED)
def test_the_mirrored_sequence_is_classified_for_a_short(
    bars: list[Bar], expected: RetestState
) -> None:
    # Reflected around the level, a resistance break downward becomes a support break, and
    # every state must come out the same.
    assert _classify(_mirror(bars), Direction.SHORT) is expected


def test_the_four_states_are_distinguishable_from_each_other() -> None:
    # "Broke and never came back" is not the same claim as "never broke", and neither is a
    # retest; a detector collapsing any two of these would still pass a laxer test.
    states = {_classify(bars, Direction.LONG) for bars, _ in _EXPECTED}

    assert len(states) == 4


# --- The tolerance band ------------------------------------------------------------


def test_a_breach_smaller_than_the_tolerance_is_not_a_break() -> None:
    # Closes reach 100.9, inside the [99, 101] band. A level is a band, not a price.
    barely = _BELOW + [(101.5, 99.5, LEVEL + BAND * 0.9)] * 8

    assert _classify(barely, Direction.LONG) is RetestState.NONE


def test_a_close_beyond_the_tolerance_is_a_break() -> None:
    # The same shape one tick further out: this one counts.
    decisive = _BELOW + [(103.0, 101.5, LEVEL + BAND * 1.1)] * 8

    assert _classify(decisive, Direction.LONG) is RetestState.BROKEN_NO_RETEST


def test_a_return_to_within_the_tolerance_counts_as_a_retest() -> None:
    # Price never touches 100 itself — its lowest low is 100.9, inside the band. That is a
    # retest: the trader watching this saw price come back to the level and hold.
    near = _BELOW + [
        (104.0, 99.5, 103.0),
        (105.0, 102.0, 104.0),
        (104.5, LEVEL + BAND * 0.9, 103.0),
        (106.0, 103.0, 105.0),
        (108.0, 105.0, 107.0),
        (110.0, 107.0, 109.0),
        (112.0, 109.0, 111.0),
        (114.0, 111.0, 113.0),
    ]

    assert _classify(near, Direction.LONG) is RetestState.RETEST_HELD


def test_a_return_that_stops_short_of_the_band_is_not_a_retest() -> None:
    # The nearest low is 101.5, outside the band; the level was never actually tested.
    shy = _BELOW + [
        (104.0, 99.5, 103.0),
        (105.0, 102.0, 104.0),
        (104.5, LEVEL + BAND * 1.5, 103.0),
        (106.0, 103.0, 105.0),
        (108.0, 105.0, 107.0),
        (110.0, 107.0, 109.0),
        (112.0, 109.0, 111.0),
        (114.0, 111.0, 113.0),
    ]

    assert _classify(shy, Direction.LONG) is RetestState.BROKEN_NO_RETEST


# --- Wicks test a level; closes decide it -------------------------------------------


def _pierced(close: float) -> list[Bar]:
    """A break, then one bar wicking far below the level and closing at ``close``."""

    return _BELOW + [
        (104.0, 99.5, 103.0),
        (106.0, 102.5, 105.0),
        (105.0, 95.0, close),  # deep wick under the level
        (104.0, 101.0, 103.0),
        (106.0, 103.0, 105.0),
        (108.0, 105.0, 107.0),
        (110.0, 107.0, 109.0),
        (112.0, 109.0, 111.0),
    ]


def test_a_level_pierced_by_a_wick_but_not_a_close_still_holds() -> None:
    assert _classify(_pierced(LEVEL + 0.5), Direction.LONG) is RetestState.RETEST_HELD


def test_a_level_lost_on_a_close_fails() -> None:
    assert _classify(_pierced(LEVEL - 2 * BAND), Direction.LONG) is RetestState.RETEST_FAILED


def test_the_wick_rule_mirrors_for_a_short() -> None:
    assert _classify(_mirror(_pierced(LEVEL + 0.5)), Direction.SHORT) is RetestState.RETEST_HELD
    assert (
        _classify(_mirror(_pierced(LEVEL - 2 * BAND)), Direction.SHORT)
        is RetestState.RETEST_FAILED
    )


# --- Degraded inputs report an absence rather than raising --------------------------


def test_a_window_shorter_than_the_lookback_reports_no_break() -> None:
    # Five bars containing an unmistakable break, judged against a 30-bar window: the
    # sequence cannot be assessed, so it is reported as absent rather than guessed at.
    short = [(98.0, 94.0, 96.0), (104.0, 99.0, 103.0), (106.0, 102.0, 105.0)]

    assert _classify(short, Direction.LONG, lookback=30) is RetestState.NONE
    assert _classify(short, Direction.LONG, lookback=3) is RetestState.BROKEN_NO_RETEST


def test_a_directionless_trade_has_no_retest_state() -> None:
    assert _classify(HELD, Direction.NONE) is RetestState.NONE


def test_an_unusable_atr_reports_no_break_rather_than_dividing_the_band_to_nothing() -> None:
    candles = _candles(HELD)

    assert detect(candles, LEVEL, Direction.LONG, 0.0, lookback=12) is RetestState.NONE
    assert detect(candles, LEVEL, Direction.LONG, float("nan"), lookback=12) is RetestState.NONE
    assert detect(candles, float("nan"), Direction.LONG, ATR, lookback=12) is RetestState.NONE


def test_only_bars_inside_the_lookback_are_examined() -> None:
    # A window of six sees only the retest and the advance after it — the break itself is
    # further back. Price being above the level is not evidence that this window watched it
    # get there, so the sequence is reported as absent rather than assumed.
    assert _classify(HELD, Direction.LONG, lookback=6) is RetestState.NONE
    assert _classify(HELD, Direction.LONG, lookback=12) is RetestState.RETEST_HELD


# --- The per-timeframe helper both callers share ------------------------------------


def _features(timeframe: str = "4h") -> TimeframeFeatures:
    return TimeframeFeatures(
        symbol="AAA",
        timeframe=timeframe,
        ema10=101.0,
        ema21=101.0,
        ema50=99.0,
        sma200=95.0,
        macd=1.0,
        macd_signal=0.5,
        macd_hist=0.5,
        atr=ATR,
        volume=1_000.0,
        relative_volume=1.4,
    )


def _structure(high: float | None = LEVEL, low: float | None = LEVEL) -> StructureState:
    return StructureState(
        structure=Structure.BULLISH,
        swing_highs=(90.0, high) if high is not None else (),
        swing_lows=(80.0, low) if low is not None else (),
    )


def test_the_level_is_resistance_for_a_long_and_support_for_a_short() -> None:
    structure = _structure(high=120.0, low=80.0)

    assert level_for(structure, Direction.LONG) == 120.0
    assert level_for(structure, Direction.SHORT) == 80.0
    assert level_for(structure, Direction.NONE) is None


def test_the_helper_classifies_each_timeframe_against_its_own_level() -> None:
    states = detect_by_timeframe(
        {"4h": _candles(HELD, timeframe="4h"), "1d": _candles(FAILED, timeframe="1d")},
        {"4h": _structure(), "1d": _structure()},
        {"4h": _features("4h"), "1d": _features("1d")},
        Direction.LONG,
        lookback=12,
        tolerance_atr=TOLERANCE_ATR,
    )

    assert states == {"4h": RetestState.RETEST_HELD, "1d": RetestState.RETEST_FAILED}


def test_a_timeframe_without_a_swing_level_is_simply_absent() -> None:
    states = detect_by_timeframe(
        {"4h": _candles(HELD)},
        {"4h": _structure(high=None)},
        {"4h": _features()},
        Direction.LONG,
        lookback=12,
    )

    assert states == {}

"""Candlestick reaction patterns, each asserted positively and negatively.

Every definition is pinned by a hand-built bar sequence. That matters more here than
anywhere else in the feature: textbook definitions vary, the trader's coaches may use
tighter ones, and a wrong definition is the kind of thing that goes unnoticed for months
because "it found a hammer" always sounds plausible. Each sequence below is built to yield
exactly one pattern, so a definition quietly widening shows up as a second name appearing.
"""

from __future__ import annotations

import pandas as pd

from trader.market_data import Candles
from trader.patterns import Pattern, detect, is_bullish

_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

# (open, high, low, close) per bar.
_Bars = list[tuple[float, float, float, float]]


def _candles(bars: _Bars) -> Candles:
    rows = [[i, o, h, low, c, 1_000.0] for i, (o, h, low, c) in enumerate(bars)]
    return Candles(symbol="AAA", timeframe="4h", frame=pd.DataFrame(rows, columns=_COLUMNS))


def _names(sequence: _Bars, **kwargs: object) -> list[str]:
    return [p.value for p in detect(_candles(sequence), **kwargs)]  # type: ignore[arg-type]


# The six sequences, each built to isolate one pattern.
BULLISH_ENGULFING: _Bars = [(105, 106, 103, 104), (103.5, 108, 103.2, 107)]
BEARISH_ENGULFING: _Bars = [(104, 106, 103, 105), (105.5, 105.8, 100, 101)]
HAMMER: _Bars = [(100, 100.6, 94, 100.4)]
SHOOTING_STAR: _Bars = [(100, 106, 99.6, 100.4)]
MORNING_STAR: _Bars = [
    (110, 110.5, 103, 104),
    (103.8, 104.4, 103.2, 103.6),
    (103.9, 108.5, 103.7, 108),
]
EVENING_STAR: _Bars = [
    (100, 107, 99.5, 106),
    (106.2, 106.8, 105.6, 106.4),
    (106.1, 106.3, 101.5, 102),
]


# --- Each pattern is found, and only it -------------------------------------------


def test_a_bullish_engulfing_is_detected_alone() -> None:
    assert _names(BULLISH_ENGULFING) == ["bullish_engulfing"]


def test_a_bearish_engulfing_is_detected_alone() -> None:
    assert _names(BEARISH_ENGULFING) == ["bearish_engulfing"]


def test_a_hammer_is_detected_alone() -> None:
    assert _names(HAMMER) == ["hammer"]


def test_a_shooting_star_is_detected_alone() -> None:
    assert _names(SHOOTING_STAR) == ["shooting_star"]


def test_a_morning_star_is_detected_alone() -> None:
    assert _names(MORNING_STAR) == ["morning_star"]


def test_an_evening_star_is_detected_alone() -> None:
    assert _names(EVENING_STAR) == ["evening_star"]


# --- The negatives, which are what stop a definition quietly widening -------------


def test_an_ordinary_candle_yields_nothing_rather_than_the_nearest_match() -> None:
    assert _names([(100, 101, 99, 100.5), (100.5, 101.5, 100, 101)]) == []


def test_a_body_merely_equal_to_the_previous_is_not_an_engulfing() -> None:
    """A bar repeating the previous open and close is a pause, not a reversal."""

    assert _names([(105, 106, 103, 104), (104, 105.2, 103.8, 105)]) == []


def test_a_body_that_does_not_cover_the_previous_is_not_an_engulfing() -> None:
    assert _names([(105, 106, 103, 104), (104.2, 105.5, 104, 104.8)]) == []


def test_an_engulfing_needs_the_previous_bar_to_be_the_opposite_colour() -> None:
    # Two rising bars: the second is bigger, but there is nothing to reverse.
    assert "bullish_engulfing" not in _names([(100, 102, 99.8, 101), (99, 108, 98.8, 107)])


def test_a_hammer_with_an_upper_wick_larger_than_its_body_is_not_a_hammer() -> None:
    assert _names([(100, 102.5, 94, 100.4)]) == []


def test_a_hammer_needs_a_wick_of_the_configured_ratio() -> None:
    # A lower wick only just longer than the body is not a rejection.
    assert _names([(100, 100.5, 99.4, 100.4)]) == []


def test_raising_the_wick_ratio_rejects_a_previously_accepted_hammer() -> None:
    assert _names(HAMMER, wick_body_ratio=2.0) == ["hammer"]
    assert _names(HAMMER, wick_body_ratio=20.0) == []


def test_a_doji_is_not_a_pin_bar() -> None:
    """With no body every wick is infinitely long and the ratio stops meaning anything."""

    assert _names([(100, 106, 94, 100)]) == []


def test_a_star_needs_a_small_middle_bar() -> None:
    # Same shape as the morning star but with a full-bodied middle: not a pause.
    assert "morning_star" not in _names(
        [(110, 110.5, 103, 104), (104, 108, 103.5, 107.8), (107.9, 111, 107.5, 110.5)]
    )


def test_a_star_needs_to_close_beyond_the_first_bars_midpoint() -> None:
    # A weak bounce that leaves the first bar intact is not a reversal.
    assert "morning_star" not in _names(
        [(110, 110.5, 103, 104), (103.8, 104.4, 103.2, 103.6), (103.9, 105, 103.7, 104.8)]
    )


# --- Degraded input reports an absence rather than raising ------------------------


def test_a_frame_too_short_for_a_three_bar_pattern_does_not_raise() -> None:
    assert _names(MORNING_STAR[:2]) == []
    assert _names(MORNING_STAR[:1]) == []


def test_an_empty_frame_yields_nothing() -> None:
    assert _names([]) == []


# --- Reporting every pattern, not the first ---------------------------------------


def test_a_bar_that_is_two_patterns_is_reported_as_both() -> None:
    """Reducing it to one would throw away the stronger reading."""

    # A hammer whose body also engulfs the previous bearish bar.
    bars: _Bars = [(101, 101.4, 100.6, 100.8), (100.6, 101.6, 96, 101.5)]
    found = _names(bars)

    assert "hammer" in found
    assert "bullish_engulfing" in found


def test_the_lookback_widens_which_bars_are_searched() -> None:
    # The reaction is two bars back, so a one-bar look finds nothing.
    bars: _Bars = [*HAMMER, (100.4, 101, 100.2, 100.8), (100.8, 101.2, 100.6, 101)]

    assert _names(bars, bars=1) == []
    assert "hammer" in _names(bars, bars=3)


# --- Direction ---------------------------------------------------------------------


def test_each_pattern_knows_which_way_it_points() -> None:
    assert is_bullish(Pattern.BULLISH_ENGULFING)
    assert is_bullish(Pattern.HAMMER)
    assert is_bullish(Pattern.MORNING_STAR)
    assert not is_bullish(Pattern.BEARISH_ENGULFING)
    assert not is_bullish(Pattern.SHOOTING_STAR)
    assert not is_bullish(Pattern.EVENING_STAR)

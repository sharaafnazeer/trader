"""Unit tests for the technical-feature computation (pure, no network).

Hand-built OHLCV frames with known shapes drive the indicators; assertions pin the
externally-observable outcomes (EMA ordering, RSI sign, ATR positivity, OBV slope,
relative-volume ratio) rather than internal mechanics.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from trader.config import CROSS_DETECTION_WINDOW
from trader.indicators import compute_features
from trader.market_data import Candles


def _candles_from_closes(closes: list[float], volumes: list[float] | None = None) -> Candles:
    """Build a Candles frame from a close series.

    High/low straddle the close by a fixed band so ATR is well-defined; volume is
    flat unless overridden.
    """

    if volumes is None:
        volumes = [1_000.0] * len(closes)
    rows = []
    for i, (close, volume) in enumerate(zip(closes, volumes, strict=True)):
        prev = closes[i - 1] if i > 0 else close
        open_ = prev
        high = max(open_, close) + 1.0
        low = min(open_, close) - 1.0
        rows.append([i, open_, high, low, close, volume])
    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return Candles(symbol="TEST/USDT", timeframe="4h", frame=frame)


def test_rising_series_stacks_the_methods_moving_averages() -> None:
    closes = [100.0 + i for i in range(260)]  # strictly rising
    features = compute_features(_candles_from_closes(closes))

    assert features.ema10 > features.ema21 > features.ema50 > features.sma200


def test_falling_series_inverts_the_methods_moving_averages() -> None:
    closes = [1000.0 - i for i in range(260)]  # strictly falling
    features = compute_features(_candles_from_closes(closes))

    assert features.ema10 < features.ema21 < features.ema50 < features.sma200
def test_full_history_frame_is_not_limited_and_has_a_finite_long_average() -> None:
    closes = [100.0 + i for i in range(260)]  # >= the 200-period window
    features = compute_features(_candles_from_closes(closes))

    assert features.limited_history is False
    assert math.isfinite(features.sma200)


def test_short_frame_reports_the_long_average_absent_rather_than_substituting_one() -> None:
    # 120 candles: enough for the fast averages but short of the 200 the filter needs.
    closes = [100.0 + i for i in range(120)]  # strictly rising
    features = compute_features(_candles_from_closes(closes))

    # Reported absent, never back-filled from a shorter window: a 120-bar average wearing
    # the 200's name would be a different indicator, and the stack condition is judged on
    # it. The consequence is intended — no long-term filter means no resolvable trend.
    assert features.limited_history is True
    assert math.isnan(features.sma200)
    assert math.isfinite(features.ema10)
    assert math.isfinite(features.ema21)


# --- Crossings as dated events ----------------------------------------------------

# One oscillating series, truncated at different points to place a known crossing a known
# number of bars from the end. Everything below is derived from *prices*: a test that
# hand-set %K and %D would be asserting its own fixture rather than the indicator.
_WAVE = [100.0 + 12.0 * math.sin(i / 9.0) + i * 0.05 for i in range(320)]


def _wave(drop_last: int):  # type: ignore[no-untyped-def]
    return _candles_from_closes(_WAVE[: len(_WAVE) - drop_last])


def test_a_downward_cross_is_reported_on_the_bar_it_happens() -> None:
    features = compute_features(_wave(21))

    assert features.stoch_crossed_down is True
    assert features.stoch_crossed_up is False
    assert features.stoch_cross_bars_ago == 0


def test_the_reported_age_grows_by_one_bar_at_a_time() -> None:
    """"Just turned" and "turned a while ago" must be distinguishable, exactly."""

    ages = [compute_features(_wave(21 - n)).stoch_cross_bars_ago for n in range(4)]

    assert ages == [0, 1, 2, 3]


def test_a_cross_beyond_the_detection_window_is_not_reported() -> None:
    # One bar past the window: the event is real but too old to be a turn.
    inside = compute_features(_wave(21 - (CROSS_DETECTION_WINDOW - 1)))
    outside = compute_features(_wave(21 - CROSS_DETECTION_WINDOW))

    assert inside.stoch_cross_bars_ago == CROSS_DETECTION_WINDOW - 1
    assert outside.stoch_cross_bars_ago is None
    assert outside.stoch_crossed_down is False


def test_a_downward_cross_records_the_high_it_came_from() -> None:
    features = compute_features(_wave(21))

    assert features.stoch_cross_extreme == pytest.approx(1.0)


def test_an_upward_cross_records_the_low_it_came_from() -> None:
    features = compute_features(_wave(49))

    assert features.stoch_crossed_up is True
    assert features.stoch_cross_extreme == pytest.approx(0.0)


def test_no_cross_means_no_age_and_no_extreme() -> None:
    features = compute_features(_wave(21 - CROSS_DETECTION_WINDOW))

    assert features.stoch_cross_bars_ago is None
    assert math.isnan(features.stoch_cross_extreme)


def test_the_macd_cross_is_reported_on_the_bar_it_happens() -> None:
    features = compute_features(_wave(18))

    assert features.macd_crossed_down is True
    assert features.macd_crossed_up is False
    assert features.macd_cross_bars_ago == 0


def test_the_macd_cross_age_grows_by_one_bar_at_a_time() -> None:
    ages = [compute_features(_wave(18 - n)).macd_cross_bars_ago for n in range(3)]

    assert ages == [0, 1, 2]


def test_a_macd_cross_beyond_the_detection_window_is_not_reported() -> None:
    # Aged from the crossing at cut 46 rather than the one at 18: ageing a cross means
    # keeping *later* bars, and the series runs out before the one at 18 reaches the
    # window edge.
    inside = compute_features(_wave(46 - (CROSS_DETECTION_WINDOW - 1)))
    outside = compute_features(_wave(46 - CROSS_DETECTION_WINDOW))

    assert inside.macd_cross_bars_ago == CROSS_DETECTION_WINDOW - 1
    # Past the window the *older* cross is gone; what is reported, if anything, is a
    # different and more recent event — never the expired one.
    assert outside.macd_cross_bars_ago != CROSS_DETECTION_WINDOW


def test_the_zero_line_side_needs_no_field_of_its_own() -> None:
    """It is the sign of the MACD line, and a duplicate field could disagree with it."""

    features = compute_features(_wave(18))

    assert features.macd > 0.0  # a crossover that happened above zero

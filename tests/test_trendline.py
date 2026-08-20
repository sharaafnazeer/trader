"""Fitting a trendline through swing pivots.

Any two points define a line and any three define one badly, so the interesting assertions
here are the refusals: too few pivots, pivots that are not really collinear, and pivots that
share a bar. Each yields **no line** rather than a weak one, because a line the trader would
never draw is worse than no line at all — it looks like evidence.
"""

from __future__ import annotations

import pytest

from trader.trendline import Trendline, distance_atr, fit


def _rising(count: int = 4, step: float = 2.0, jitter: float = 0.0) -> list[tuple[int, float]]:
    return [(i * 10, 100.0 + i * step + (jitter if i % 2 else -jitter)) for i in range(count)]


# --- The fit ----------------------------------------------------------------------


def test_ascending_pivots_fit_a_rising_line() -> None:
    line = fit(_rising(), latest_index=40)

    assert line is not None
    assert line.is_rising
    assert not line.is_falling
    assert line.touches == 4
    # Four pivots ten bars apart rising 2.0 each: slope 0.2/bar, so 108 at bar 40.
    assert line.slope == pytest.approx(0.2)
    assert line.level_now == pytest.approx(108.0)


def test_descending_pivots_fit_a_falling_line() -> None:
    pivots = [(i * 10, 100.0 - i * 2.0) for i in range(4)]

    line = fit(pivots, latest_index=40)

    assert line is not None
    assert line.is_falling
    assert line.slope == pytest.approx(-0.2)
    assert line.level_now == pytest.approx(92.0)


def test_perfectly_collinear_pivots_report_the_maximum_fit_quality() -> None:
    line = fit(_rising(), latest_index=40)

    assert line is not None
    assert line.r_squared == pytest.approx(1.0)


def test_flat_pivots_are_a_perfect_fit_rather_than_undefined() -> None:
    """No variance to explain does not mean the line is a bad one."""

    line = fit([(0, 100.0), (10, 100.0), (20, 100.0)], latest_index=30)

    assert line is not None
    assert line.r_squared == pytest.approx(1.0)
    assert line.slope == pytest.approx(0.0)


# --- The refusals -------------------------------------------------------------------


def test_scattered_pivots_fall_below_the_threshold_and_yield_no_line() -> None:
    scattered = [(0, 100.0), (10, 130.0), (20, 95.0), (30, 140.0)]

    assert fit(scattered, latest_index=40) is None


def test_the_fit_threshold_is_what_decides_it() -> None:
    scattered = [(0, 100.0), (10, 130.0), (20, 95.0), (30, 140.0)]

    assert fit(scattered, latest_index=40, min_r_squared=0.7) is None
    # Accept any fit at all and the same pivots produce a line — which is exactly why the
    # threshold exists rather than the fit being trusted by default.
    assert fit(scattered, latest_index=40, min_r_squared=0.0) is not None


def test_fewer_pivots_than_the_minimum_yields_no_line_rather_than_an_error() -> None:
    assert fit(_rising(count=2), latest_index=20) is None
    assert fit(_rising(count=1), latest_index=10) is None
    assert fit([], latest_index=10) is None


def test_two_points_never_make_a_trendline_however_well_they_fit() -> None:
    """They fit perfectly by definition and prove nothing."""

    assert fit(_rising(count=2), latest_index=20, min_r_squared=0.0) is None


def test_raising_the_minimum_touches_rejects_a_previously_accepted_line() -> None:
    assert fit(_rising(count=4), latest_index=40, min_touches=3) is not None
    assert fit(_rising(count=4), latest_index=40, min_touches=5) is None


def test_pivots_sharing_one_bar_have_no_line_through_them() -> None:
    assert fit([(10, 100.0), (10, 105.0), (10, 110.0)], latest_index=20) is None


def test_only_the_most_recent_pivots_are_fitted() -> None:
    """A trendline is a claim about the trend in force now, not about all of history."""

    ancient = [(0, 500.0), (10, 400.0)]
    recent = [(20, 100.0), (30, 102.0), (40, 104.0)]

    line = fit([*ancient, *recent], latest_index=50, max_pivots=3)

    assert line is not None
    assert line.touches == 3
    assert line.slope == pytest.approx(0.2)


# --- Distance ------------------------------------------------------------------------


def test_distance_is_signed_and_measured_in_atr() -> None:
    line = Trendline(slope=0.2, level_now=100.0, touches=4, r_squared=1.0)

    assert distance_atr(line, close=104.0, atr=2.0) == pytest.approx(2.0)
    assert distance_atr(line, close=96.0, atr=2.0) == pytest.approx(-2.0)


def test_the_same_gap_is_a_smaller_distance_when_volatility_is_higher() -> None:
    line = Trendline(slope=0.2, level_now=100.0, touches=4, r_squared=1.0)

    quiet = distance_atr(line, close=104.0, atr=1.0)
    volatile = distance_atr(line, close=104.0, atr=4.0)

    assert quiet == pytest.approx(4.0)
    assert volatile == pytest.approx(1.0)


def test_an_unusable_atr_yields_no_distance_rather_than_a_fabricated_zero() -> None:
    import math

    line = Trendline(slope=0.2, level_now=100.0, touches=4, r_squared=1.0)

    assert math.isnan(distance_atr(line, close=104.0, atr=0.0))
    assert math.isnan(distance_atr(line, close=104.0, atr=float("nan")))

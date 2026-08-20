"""Pins the deterministic backtest fixture, so later scoring or replay drift fails here.

Every task in the entry-quality feature carries a quality gate of the form "with the flag
off, the fixture reproduces exactly". This module is what that gate means. If a change to
the scoring model, the direction rule, the trade planner or the replay path alters a single
number below, this test fails and the change has to be justified rather than discovered
later as an unexplained shift in a delta table.
"""

from __future__ import annotations

import pytest

from baseline_fixture import FIXTURE_CONFIG, build_history
from trader.backtester import Backtester
from trader.metrics import summarize

# Re-pinned 2026-08-20. The previous figures (16 trades, 0.875 win rate, +1.6174R) were
# taken against the EMA 20/50/200 direction rule and the 0-100 quality threshold. Both were
# replaced when the indicator set was closed to the trader's method — direction is now
# ``EMA10 > EMA21 > EMA50 > SMA200`` and there is no threshold — so different moments
# qualify and the fixture trades 14 times instead of 16. The drift is the intended change,
# not a regression; from here these numbers guard against unintended drift as before.
#
# A relative tolerance is used rather than exact equality so a floating-point difference
# in a dependency cannot fail the build, while behavioural drift comfortably exceeds it.
TOLERANCE = 1e-9


@pytest.fixture(scope="module")
def report():  # type: ignore[no-untyped-def]
    histories, btc = build_history()
    return summarize(Backtester().run(FIXTURE_CONFIG, histories, btc).outcomes)


def test_the_fixture_actually_trades(report) -> None:  # type: ignore[no-untyped-def]
    # The whole point: a fixture pinning zero trades would be satisfied by any change.
    assert report.resolved_trades > 0
    assert report.resolved_trades == 14
    assert report.unresolved_trades == 0


def test_the_fixture_contains_both_wins_and_losses(report) -> None:  # type: ignore[no-untyped-def]
    # Without both, expectancy and profit factor could not drift detectably.
    assert report.wins == 12
    assert report.losses == 2


def test_the_fixture_covers_both_directions_and_both_coins(report) -> None:  # type: ignore[no-untyped-def]
    assert {b.key: b.resolved_trades for b in report.per_direction} == {"LONG": 7, "SHORT": 7}
    assert {b.key: b.resolved_trades for b in report.per_coin} == {"UPCOIN": 7, "DOWNCOIN": 7}


def test_the_fixture_metrics_are_pinned(report) -> None:  # type: ignore[no-untyped-def]
    assert report.win_rate == pytest.approx(0.8571428571428571, rel=TOLERANCE)
    assert report.expectancy == pytest.approx(1.564596617599854, rel=TOLERANCE)
    assert report.profit_factor == pytest.approx(11.899368743485557, rel=TOLERANCE)
    assert report.max_drawdown == pytest.approx(0.019995928904160798, rel=TOLERANCE)
    assert report.avg_win == pytest.approx(1.992836889312356, rel=TOLERANCE)
    assert report.avg_loss == pytest.approx(-1.0048450126751591, rel=TOLERANCE)


def test_the_fixture_is_deterministic() -> None:
    # Two independent builds must agree, or nothing above means anything.
    first = summarize(Backtester().run(FIXTURE_CONFIG, *build_history()).outcomes)
    second = summarize(Backtester().run(FIXTURE_CONFIG, *build_history()).outcomes)

    assert first.resolved_trades == second.resolved_trades
    assert first.expectancy == second.expectancy
    assert first.max_drawdown == second.max_drawdown

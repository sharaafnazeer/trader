"""Unit tests for the minimal backtest metrics (resolved count + win rate)."""

from __future__ import annotations

from trader.direction import Direction
from trader.metrics import summarize
from trader.trade_simulator import TradeOutcome, TradeResult


def _outcome(result: TradeResult) -> TradeOutcome:
    return TradeOutcome(
        symbol="AAA",
        direction=Direction.LONG,
        entry_time=0,
        entry_price=100.0,
        exit_time=1 if result is not TradeResult.UNRESOLVED else None,
        exit_price=110.0 if result is not TradeResult.UNRESOLVED else None,
        result=result,
    )


def test_win_rate_over_resolved_trades() -> None:
    report = summarize(
        [
            _outcome(TradeResult.WIN),
            _outcome(TradeResult.WIN),
            _outcome(TradeResult.LOSS),
        ]
    )
    assert report.resolved_trades == 3
    assert report.wins == 2
    assert report.losses == 1
    assert report.win_rate == 2 / 3


def test_unresolved_trades_are_excluded_from_win_rate() -> None:
    report = summarize(
        [
            _outcome(TradeResult.WIN),
            _outcome(TradeResult.LOSS),
            _outcome(TradeResult.UNRESOLVED),
        ]
    )
    assert report.resolved_trades == 2
    assert report.unresolved_trades == 1
    assert report.win_rate == 0.5


def test_no_resolved_trades_yields_zero_win_rate() -> None:
    report = summarize([_outcome(TradeResult.UNRESOLVED)])
    assert report.resolved_trades == 0
    assert report.win_rate == 0.0
    assert report.unresolved_trades == 1

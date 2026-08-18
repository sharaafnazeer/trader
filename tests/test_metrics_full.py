"""Unit tests for the full backtest report on pinned trade sets.

Hand-computed expectancy, profit factor, average/largest win-loss, resolved/unresolved
counts, the fixed-fractional equity curve and its maximum drawdown, and the per-coin /
per-direction partitions. Deterministic and network-free.
"""

from __future__ import annotations

import pytest

from trader.direction import Direction
from trader.metrics import summarize
from trader.trade_simulator import TradeOutcome, TradeResult


def _outcome(
    symbol: str,
    direction: Direction,
    result: TradeResult,
    r_multiple: float,
    entry_time: int,
) -> TradeOutcome:
    resolved = result is not TradeResult.UNRESOLVED
    return TradeOutcome(
        symbol=symbol,
        direction=direction,
        entry_time=entry_time,
        entry_price=100.0,
        exit_time=entry_time + 1 if resolved else None,
        exit_price=110.0 if resolved else None,
        result=result,
        r_multiple=r_multiple if resolved else 0.0,
    )


def _pinned() -> list[TradeOutcome]:
    # Two wins (+2R each) and two losses (-1R each), interleaved by entry time, plus one
    # unresolved trade that must not affect any win/loss statistic.
    return [
        _outcome("AAA", Direction.LONG, TradeResult.WIN, 2.0, entry_time=1),
        _outcome("AAA", Direction.SHORT, TradeResult.LOSS, -1.0, entry_time=2),
        _outcome("BBB", Direction.LONG, TradeResult.WIN, 2.0, entry_time=3),
        _outcome("BBB", Direction.SHORT, TradeResult.LOSS, -1.0, entry_time=4),
        _outcome("AAA", Direction.LONG, TradeResult.UNRESOLVED, 0.0, entry_time=5),
    ]


def test_headline_metrics_match_hand_computed_values() -> None:
    report = summarize(_pinned(), risk_per_trade=0.1)

    assert report.resolved_trades == 4
    assert report.wins == 2
    assert report.losses == 2
    assert report.unresolved_trades == 1
    assert report.win_rate == pytest.approx(0.5)
    # expectancy = mean R over resolved = (2 - 1 + 2 - 1) / 4 = 0.5
    assert report.expectancy == pytest.approx(0.5)
    # profit factor = gross win R / gross loss R = 4 / 2 = 2.0
    assert report.profit_factor == pytest.approx(2.0)
    assert report.avg_win == pytest.approx(2.0)
    assert report.avg_loss == pytest.approx(-1.0)
    assert report.largest_win == pytest.approx(2.0)
    assert report.largest_loss == pytest.approx(-1.0)


def test_equity_curve_and_max_drawdown() -> None:
    report = summarize(_pinned(), risk_per_trade=0.1)

    # Fixed-fractional curve (start 1.0, risk 10% of running equity * R each trade):
    #   1.0 -> +0.2 -> 1.2 -> -0.12 -> 1.08 -> +0.216 -> 1.296 -> -0.1296 -> 1.1664
    assert report.equity_curve == pytest.approx((1.0, 1.2, 1.08, 1.296, 1.1664))
    # Largest peak-to-trough decline: 1.2 -> 1.08 is (1.2-1.08)/1.2 = 0.10.
    assert report.max_drawdown == pytest.approx(0.10)


def test_profit_factor_is_infinite_with_no_losses() -> None:
    report = summarize(
        [_outcome("AAA", Direction.LONG, TradeResult.WIN, 2.0, entry_time=1)],
        risk_per_trade=0.1,
    )
    assert report.profit_factor == float("inf")


def test_per_coin_and_per_direction_partition_the_trades() -> None:
    report = summarize(_pinned(), risk_per_trade=0.1)

    # Per-coin counts sum to the resolved total, and each coin has one win + one loss.
    assert {b.key: b.resolved_trades for b in report.per_coin} == {"AAA": 2, "BBB": 2}
    assert sum(b.resolved_trades for b in report.per_coin) == report.resolved_trades

    # Per-direction: both wins are LONG, both losses are SHORT.
    per_dir = {b.key: b for b in report.per_direction}
    assert per_dir["LONG"].wins == 2 and per_dir["LONG"].losses == 0
    assert per_dir["SHORT"].wins == 0 and per_dir["SHORT"].losses == 2
    assert sum(b.resolved_trades for b in report.per_direction) == report.resolved_trades


def test_determinism_identical_inputs_identical_report() -> None:
    a = summarize(_pinned(), risk_per_trade=0.1)
    b = summarize(_pinned(), risk_per_trade=0.1)
    assert a == b

"""Unit tests for the trade-outcome simulator.

Pinned finer-bar sequences (no network): target-first -> WIN, stop-first -> LOSS, a
single bar spanning both -> LOSS (the conservative stop-first tie-break), and neither
touched -> UNRESOLVED. Both long and short directions are covered, plus the cost model
(net vs zero-cost gross, R-multiple correctness) and the optional max-holding time-stop.
"""

from __future__ import annotations

import pandas as pd
import pytest

from trader.direction import Direction
from trader.market_data import Candles
from trader.trade_simulator import EntrySetup, TradeResult, TradeSimulator


def _bars(rows: list[tuple[float, float]]) -> Candles:
    """Build finer bars from ``(high, low)`` pairs on a 1-minute grid."""
    data = [
        [i * 60_000, (h + low) / 2, h, low, (h + low) / 2, 1.0]
        for i, (h, low) in enumerate(rows)
    ]
    frame = pd.DataFrame(
        data, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    return Candles(symbol="AAA", timeframe="1m", frame=frame)


def _long() -> EntrySetup:
    return EntrySetup("AAA", Direction.LONG, entry_time=0, entry=100.0, stop=95.0, target=110.0)


def _short() -> EntrySetup:
    return EntrySetup("AAA", Direction.SHORT, entry_time=0, entry=100.0, stop=105.0, target=90.0)


def test_long_target_first_is_a_win() -> None:
    # First bar drifts, second bar's high reaches the target before any stop touch.
    bars = _bars([(102.0, 99.0), (111.0, 108.0)])
    outcome = TradeSimulator().simulate(_long(), bars)
    assert outcome.result is TradeResult.WIN
    assert outcome.exit_price == 110.0
    assert outcome.exit_time == 60_000


def test_long_stop_first_is_a_loss() -> None:
    bars = _bars([(102.0, 99.0), (103.0, 94.0)])
    outcome = TradeSimulator().simulate(_long(), bars)
    assert outcome.result is TradeResult.LOSS
    assert outcome.exit_price == 95.0
    assert outcome.exit_time == 60_000


def test_long_same_bar_span_is_a_loss() -> None:
    # A single bar whose range spans BOTH stop and target counts as a loss (stop-first).
    bars = _bars([(115.0, 90.0)])
    outcome = TradeSimulator().simulate(_long(), bars)
    assert outcome.result is TradeResult.LOSS
    assert outcome.exit_price == 95.0


def test_long_unresolved_when_neither_touched() -> None:
    bars = _bars([(101.0, 99.0), (102.0, 98.0)])
    outcome = TradeSimulator().simulate(_long(), bars)
    assert outcome.result is TradeResult.UNRESOLVED
    assert outcome.exit_time is None
    assert outcome.exit_price is None


def test_short_target_first_is_a_win() -> None:
    bars = _bars([(101.0, 98.0), (92.0, 89.0)])
    outcome = TradeSimulator().simulate(_short(), bars)
    assert outcome.result is TradeResult.WIN
    assert outcome.exit_price == 90.0


def test_short_same_bar_span_is_a_loss() -> None:
    bars = _bars([(106.0, 88.0)])
    outcome = TradeSimulator().simulate(_short(), bars)
    assert outcome.result is TradeResult.LOSS
    assert outcome.exit_price == 105.0


def _closed_bars(rows: list[tuple[float, float, float]]) -> Candles:
    """Build finer bars from ``(high, low, close)`` triples on a 1-minute grid."""
    data = [
        [i * 60_000, close, h, low, close, 1.0] for i, (h, low, close) in enumerate(rows)
    ]
    frame = pd.DataFrame(
        data, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    return Candles(symbol="AAA", timeframe="1m", frame=frame)


# --------------------------------------------------------------------------------------
# Costs — net by default, gross at zero cost, and R-multiple correctness.
# --------------------------------------------------------------------------------------


def test_zero_cost_win_r_multiple_equals_target_to_risk_ratio() -> None:
    # entry 100, stop 90 (R=10), target 120 (reward 20) -> gross R-multiple = 2.0.
    setup = EntrySetup("AAA", Direction.LONG, entry_time=0, entry=100.0, stop=90.0, target=120.0)
    bars = _bars([(121.0, 118.0)])  # first bar reaches the target
    outcome = TradeSimulator().simulate(setup, bars, fee_rate=0.0, slippage=0.0)
    assert outcome.result is TradeResult.WIN
    assert outcome.r_multiple == pytest.approx(2.0)
    assert outcome.costs == pytest.approx(0.0)


def test_costs_reduce_the_winning_r_multiple_net_of_fees_and_slippage() -> None:
    setup = EntrySetup("AAA", Direction.LONG, entry_time=0, entry=100.0, stop=90.0, target=120.0)
    bars = _bars([(121.0, 118.0)])

    gross = TradeSimulator().simulate(setup, bars, fee_rate=0.0, slippage=0.0)
    net = TradeSimulator().simulate(setup, bars, fee_rate=0.001, slippage=0.002)

    # Hand-computed net R-multiple for the known path (see _apply_costs):
    #   fill_entry = 100 * 1.002 = 100.2 ; fill_exit = 120 * 0.998 = 119.76
    #   fee = 0.001*100.2 + 0.001*119.76 = 0.21996
    #   net_pnl = 119.76 - 100.2 - 0.21996 = 19.34004 ; R = 10 -> 1.934004
    assert net.r_multiple == pytest.approx(1.934004)
    assert net.r_multiple < gross.r_multiple
    assert net.costs == pytest.approx(20.0 - 19.34004)  # gross_pnl - net_pnl


def test_unresolved_trade_has_zero_r_multiple_and_costs() -> None:
    bars = _bars([(101.0, 99.0), (102.0, 98.0)])
    outcome = TradeSimulator().simulate(_long(), bars, fee_rate=0.001, slippage=0.002)
    assert outcome.result is TradeResult.UNRESOLVED
    assert outcome.r_multiple == 0.0
    assert outcome.costs == 0.0


# --------------------------------------------------------------------------------------
# Time-stop — exit at market after max_holding_bars when neither level is hit.
# --------------------------------------------------------------------------------------


def test_max_holding_exits_at_market_after_the_cap() -> None:
    # Price drifts sideways, never touching stop (95) or target (110); the third bar's
    # close is the market exit when the cap is 3.
    bars = _closed_bars([(101.0, 99.0, 100.0), (102.0, 98.0, 100.5), (103.0, 97.0, 101.0)])
    outcome = TradeSimulator().simulate(
        _long(), bars, fee_rate=0.0, slippage=0.0, max_holding_bars=3
    )

    assert outcome.exit_time == 2 * 60_000  # the third (index 2) bar
    assert outcome.exit_price == 101.0  # that bar's close, at market
    # A profitable market exit is a win; its R-multiple reflects the market close.
    assert outcome.result is TradeResult.WIN
    assert outcome.r_multiple == pytest.approx((101.0 - 100.0) / 5.0)


def test_max_holding_market_exit_at_a_loss_is_a_loss() -> None:
    # Sideways-to-down, never hitting either level; market exit below entry -> loss.
    bars = _closed_bars([(101.0, 99.0, 100.0), (100.5, 98.5, 99.0)])
    outcome = TradeSimulator().simulate(
        _long(), bars, fee_rate=0.0, slippage=0.0, max_holding_bars=2
    )

    assert outcome.exit_time == 60_000
    assert outcome.exit_price == 99.0
    assert outcome.result is TradeResult.LOSS
    assert outcome.r_multiple == pytest.approx((99.0 - 100.0) / 5.0)


def test_level_hit_before_time_stop_still_resolves_on_the_level() -> None:
    # The target is reached on bar 2, before the max-holding cap of 5 would trigger.
    bars = _bars([(102.0, 99.0), (111.0, 108.0), (112.0, 109.0)])
    outcome = TradeSimulator().simulate(_long(), bars, max_holding_bars=5)
    assert outcome.result is TradeResult.WIN
    assert outcome.exit_price == 110.0

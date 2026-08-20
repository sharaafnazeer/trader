"""The reward-to-risk preference: structural targets versus manufactured ones.

The planner *floors* every take-profit at the configured ratio, so ``risk_reward`` is at
least the target by construction and says nothing about the setup. What distinguishes one
plan from another is whether that target is a swing level the market has respected or one
placed purely to satisfy the arithmetic.

This is a **preference, not a gate** — decided deliberately and recorded in
``.sdd/strategy-catalogue/rules.md``. Gating on it would reject every coin at new highs,
which by definition has no level above it, so it orders setups and never excludes one.
"""

from __future__ import annotations

import pandas as pd
import pytest

from trader.direction import Direction
from trader.market_data import Candles
from trader.structure import Structure, StructureState
from trader.trade_planner import TradePlanner

ATR = 2.0
_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def _candles(close: float) -> Candles:
    rows = [[i, close, close + 1.0, close - 1.0, close, 1_000.0] for i in range(3)]
    return Candles(symbol="AAA", timeframe="4h", frame=pd.DataFrame(rows, columns=_COLUMNS))


def _plan(close: float, highs: tuple[float, ...], lows: tuple[float, ...] = (90.0,)):  # type: ignore[no-untyped-def]
    pivots = StructureState(structure=Structure.BULLISH, swing_highs=highs, swing_lows=lows)
    plan = TradePlanner().plan(
        Direction.LONG, _candles(close), pivots, ATR, atr_buffer=0.5, target_rr=2.0
    )
    assert plan is not None
    return plan


def test_a_far_enough_pivot_is_the_target_and_is_marked_structural() -> None:
    # Entry 100, stop 89 (swing low 90 less 0.5 ATR), so risk 11 and the 2R floor is 122.
    # A pivot at 150 clears it, so the plan aims at the pivot.
    plan = _plan(100.0, highs=(150.0,))

    assert plan.take_profit == pytest.approx(150.0)
    assert plan.target_is_structural is True
    assert plan.risk_reward > 2.0


def test_a_pivot_too_close_is_replaced_by_the_floor_and_marked_manufactured() -> None:
    plan = _plan(100.0, highs=(105.0,))

    assert plan.take_profit == pytest.approx(122.0)
    assert plan.target_is_structural is False
    assert plan.risk_reward == pytest.approx(2.0)


def test_a_coin_at_new_highs_has_no_pivot_and_is_marked_manufactured() -> None:
    """The case a hard gate would reject forever: there is nothing above the price."""

    plan = _plan(100.0, highs=(80.0,))

    assert plan.target_is_structural is False
    assert plan.risk_reward == pytest.approx(2.0)


def test_both_kinds_still_satisfy_the_configured_ratio() -> None:
    """1:2 remains guaranteed — the flag distinguishes *how*, not *whether*."""

    structural = _plan(100.0, highs=(150.0,))
    manufactured = _plan(100.0, highs=(105.0,))

    assert structural.risk_reward >= 2.0
    assert manufactured.risk_reward >= 2.0


def test_the_short_mirror_marks_its_target_the_same_way() -> None:
    pivots = StructureState(
        structure=Structure.BEARISH, swing_highs=(110.0,), swing_lows=(50.0,)
    )
    plan = TradePlanner().plan(
        Direction.SHORT, _candles(100.0), pivots, ATR, atr_buffer=0.5, target_rr=2.0
    )
    assert plan is not None

    # Stop 111 (swing high plus 0.5 ATR), risk 11, floor at 78; the pivot at 50 clears it.
    assert plan.take_profit == pytest.approx(50.0)
    assert plan.target_is_structural is True

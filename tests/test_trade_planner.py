"""Unit tests for the TradePlanner and the Risk-to-reward scoring fraction.

Candles and structure are hand-built so entry, invalidation, and the swing targets
are pinned by their inputs — no network, no candle fabrication beyond a close column.
"""

from __future__ import annotations

import pandas as pd

from trader.direction import Direction
from trader.market_data import Candles
from trader.scoring_model import _rr_fraction
from trader.structure import Structure, StructureState
from trader.trade_planner import TradePlanner

ATR = 4.0
ATR_BUFFER = 0.5
TARGET_RR = 2.0


def _candles(latest_close: float) -> Candles:
    """A minimal candle frame whose last close is ``latest_close``."""
    frame = pd.DataFrame(
        {
            "timestamp": [1, 2, 3],
            "open": [latest_close, latest_close, latest_close],
            "high": [latest_close, latest_close, latest_close],
            "low": [latest_close, latest_close, latest_close],
            "close": [latest_close - 2.0, latest_close - 1.0, latest_close],
            "volume": [1.0, 1.0, 1.0],
        }
    )
    return Candles(symbol="X", timeframe="4h", frame=frame)


def _structure(
    *, swing_highs: tuple[float, ...], swing_lows: tuple[float, ...], bullish: bool
) -> StructureState:
    state = Structure.BULLISH if bullish else Structure.BEARISH
    return StructureState(structure=state, swing_highs=swing_highs, swing_lows=swing_lows)


def test_long_stop_is_below_invalidation_by_the_atr_buffer() -> None:
    pivots = _structure(swing_highs=(130.0, 140.0), swing_lows=(95.0, 90.0), bullish=True)

    plan = TradePlanner().plan(
        Direction.LONG, _candles(100.0), pivots, ATR, atr_buffer=ATR_BUFFER, target_rr=TARGET_RR
    )

    assert plan is not None
    assert plan.entry == 100.0
    assert plan.invalidation == 90.0  # last swing low
    # Stop sits below the invalidation by exactly atr_buffer * ATR.
    assert plan.stop_loss == 90.0 - ATR_BUFFER * ATR  # 88.0
    assert plan.stop_loss < plan.invalidation


def test_short_stop_is_above_invalidation_by_the_atr_buffer() -> None:
    pivots = _structure(swing_highs=(105.0, 110.0), swing_lows=(70.0, 60.0), bullish=False)

    plan = TradePlanner().plan(
        Direction.SHORT, _candles(100.0), pivots, ATR, atr_buffer=ATR_BUFFER, target_rr=TARGET_RR
    )

    assert plan is not None
    assert plan.invalidation == 110.0  # last swing high
    # Stop sits above the invalidation by exactly atr_buffer * ATR.
    assert plan.stop_loss == 110.0 + ATR_BUFFER * ATR  # 112.0
    assert plan.stop_loss > plan.invalidation


def test_long_take_profit_meets_target_and_risk_reward_matches_geometry() -> None:
    pivots = _structure(swing_highs=(130.0, 140.0), swing_lows=(95.0, 90.0), bullish=True)

    plan = TradePlanner().plan(
        Direction.LONG, _candles(100.0), pivots, ATR, atr_buffer=ATR_BUFFER, target_rr=TARGET_RR
    )

    assert plan is not None
    risk = plan.entry - plan.stop_loss  # 12.0
    # Nearest resistance above entry (130) already beats the 2:1 floor (124), so it is used.
    assert plan.take_profit == 130.0
    assert plan.risk_reward >= TARGET_RR
    assert plan.risk_reward == (plan.take_profit - plan.entry) / risk


def test_long_take_profit_is_floored_to_target_when_resistance_too_close() -> None:
    # Nearest resistance (105) is closer than the 2:1 floor, so the target is pushed out.
    pivots = _structure(swing_highs=(105.0, 108.0), swing_lows=(95.0, 90.0), bullish=True)

    plan = TradePlanner().plan(
        Direction.LONG, _candles(100.0), pivots, ATR, atr_buffer=ATR_BUFFER, target_rr=TARGET_RR
    )

    assert plan is not None
    risk = plan.entry - plan.stop_loss  # 12.0
    assert plan.take_profit == plan.entry + TARGET_RR * risk  # 124.0 (the floor)
    assert plan.risk_reward == TARGET_RR


def test_plan_is_none_without_direction_or_invalidation() -> None:
    pivots = _structure(swing_highs=(130.0,), swing_lows=(90.0,), bullish=True)
    empty = _structure(swing_highs=(), swing_lows=(), bullish=True)

    assert TradePlanner().plan(Direction.NONE, _candles(100.0), pivots, ATR) is None
    # A long with no swing low has no invalidation level, so no plan can be built.
    assert TradePlanner().plan(Direction.LONG, _candles(100.0), empty, ATR) is None


def test_risk_reward_fraction_full_at_target_reduced_below_and_zero_without_plan() -> None:
    assert _rr_fraction(TARGET_RR, TARGET_RR) == 1.0  # exactly at target -> full
    assert _rr_fraction(3.0, TARGET_RR) == 1.0  # above target -> full (clamped)
    assert _rr_fraction(1.0, TARGET_RR) == 0.5  # half the target -> half credit
    assert _rr_fraction(None, TARGET_RR) == 0.0  # no plan -> no credit

"""Strategy 2 — the breakout family.

The cases the trader's material singles out: a convincing break versus a wick through the
level, the false breakout that must not read as a setup, and the already-run-away break
whose answer is "wait for the retest" rather than "no setup".
"""

from __future__ import annotations

import math

import pandas as pd

from trader.breakout import (
    BREAKOUT_CONDITION_COUNT,
    CONDITION_BREAK,
    CONDITION_BREAK_CANDLE,
    CONDITION_BREAK_VOLUME,
    CONDITION_CONTINUATION,
    CONDITION_LEVEL,
    CONDITION_NOT_EXTENDED,
    CONDITION_ROOM,
    LevelKind,
    evaluate_breakout,
    find_levels,
)
from trader.direction import Direction
from trader.indicators import TimeframeFeatures
from trader.market_data import OHLCV_COLUMNS, Candles
from trader.strategy import Condition, EntryStatus, SetupVerdict, StrategyId
from trader.structure import Structure, StructureState

_AVERAGE_VOLUME = 1_200_000.0
_EXPANDED_VOLUME = 2_400_000.0


def _features(atr: float = 2.0) -> TimeframeFeatures:
    return TimeframeFeatures(
        symbol="AAA",
        timeframe="4h",
        ema10=99.0,
        ema21=98.0,
        ema50=97.0,
        sma200=90.0,
        macd=0.5,
        macd_signal=0.2,
        macd_hist=0.3,
        atr=atr,
        volume=_EXPANDED_VOLUME,
        relative_volume=2.0,
        stoch_rsi_k=0.6,
        stoch_rsi_d=0.5,
        macd_crossed_up=True,
        macd_crossed_down=False,
        macd_cross_bars_ago=1,
        stoch_crossed_up=True,
        stoch_crossed_down=False,
        stoch_cross_bars_ago=1,
        stoch_cross_extreme=0.15,
        limited_history=False,
    )


def _structure(
    structure: Structure = Structure.BULLISH,
    highs: tuple[float, ...] = (99.9, 100.1, 100.0),
    lows: tuple[float, ...] = (94.0, 95.0),
) -> StructureState:
    return StructureState(
        structure=structure,
        swing_highs=highs,
        swing_lows=lows,
        swing_high_indices=tuple(range(len(highs))),
        swing_low_indices=tuple(range(len(lows))),
    )


def _history(bars: int = 20, volume: float = _AVERAGE_VOLUME) -> list[list[float]]:
    """Bars pressing up against the level without closing beyond it."""

    return [
        [float(i) * 1000.0, 97.0 + i * 0.1, 98.0 + i * 0.1, 96.5 + i * 0.1, 97.5 + i * 0.1, volume]
        for i in range(bars)
    ]


def _candles(rows: list[list[float]]) -> Candles:
    return Candles(symbol="AAA", timeframe="4h", frame=pd.DataFrame(rows, columns=OHLCV_COLUMNS))


def _evaluate(
    last: list[float],
    *,
    structure: StructureState | None = None,
    atr: float = 2.0,
) -> SetupVerdict:
    return evaluate_breakout(
        "AAA",
        _candles(_history() + [last]),
        _features(atr),
        structure if structure is not None else _structure(),
    )


def _row(verdict: SetupVerdict, name: str) -> Condition:
    """The named condition, asserting it was judged at all."""

    row = verdict.condition(name)
    assert row is not None, f"{name} was not among the judged conditions"
    return row


# --- Levels ------------------------------------------------------------------------


def test_repeated_pivots_at_one_price_make_one_established_level() -> None:
    levels = find_levels(_structure(), atr=2.0)

    resistance = [lvl for lvl in levels if lvl.kind is LevelKind.RESISTANCE]
    assert len(resistance) == 1
    assert resistance[0].touches == 3
    assert math.isclose(resistance[0].price, 100.0, abs_tol=0.1)


def test_a_single_pivot_is_not_a_level() -> None:
    """One touch is a place price turned; the strategy will not trade a break of it."""

    levels = find_levels(_structure(highs=(100.0,), lows=(94.0,)), atr=2.0)

    assert levels == ()


def test_pivots_further_apart_than_the_tolerance_are_separate_levels() -> None:
    levels = find_levels(
        _structure(highs=(100.0, 100.1, 130.0, 130.2), lows=()), atr=2.0
    )

    assert sorted(round(lvl.price) for lvl in levels) == [100, 130]


def test_no_levels_without_a_readable_atr() -> None:
    assert find_levels(_structure(), atr=float("nan")) == ()
    assert find_levels(_structure(), atr=0.0) == ()


# --- The trader's worked examples --------------------------------------------------


def test_the_convincing_breakout_is_ready() -> None:
    """Close 2.2 clear of the level on 2x volume — the example's own numbers."""

    verdict = _evaluate([20000.0, 99.4, 102.8, 99.2, 102.2, _EXPANDED_VOLUME])

    assert verdict.strategy is StrategyId.BREAKOUT_LONG
    assert verdict.entry_status is EntryStatus.READY
    assert verdict.decision is Direction.LONG
    assert verdict.conditions_evaluated == BREAKOUT_CONDITION_COUNT


def test_a_wick_through_the_level_that_closes_back_under_is_not_a_breakout() -> None:
    """High 101, close 99.50 — the material's counter-example."""

    verdict = _evaluate([20000.0, 99.4, 101.0, 99.2, 99.5, _EXPANDED_VOLUME])

    assert verdict.entry_status is EntryStatus.NOT_READY
    assert verdict.decision is Direction.NONE
    assert _row(verdict, CONDITION_BREAK).met is False


def test_the_false_breakout_reports_the_level_holding() -> None:
    """Wick to 103, close 98.8. The level held — evidence *for* it, not against."""

    verdict = _evaluate([20000.0, 99.0, 103.0, 98.5, 98.8, _EXPANDED_VOLUME])

    assert verdict.entry_status is EntryStatus.NOT_READY
    detail = _row(verdict, CONDITION_BREAK).detail
    assert "held" in detail
    assert "evidence for the level" in detail


def test_a_break_that_already_ran_away_says_wait_for_the_retest() -> None:
    """Not "no setup" — the level is still the level, and the retest is Strategy 3."""

    verdict = _evaluate([20000.0, 99.4, 108.5, 99.2, 108.0, _EXPANDED_VOLUME])

    assert verdict.strategy is StrategyId.BREAKOUT_LONG
    assert verdict.entry_status is EntryStatus.NOT_READY
    assert "wait for the retest" in _row(verdict, CONDITION_NOT_EXTENDED).detail
    assert "chasing" in _row(verdict, CONDITION_NOT_EXTENDED).detail


# --- The individual conditions -----------------------------------------------------


def test_volume_must_expand_on_the_break() -> None:
    verdict = _evaluate([20000.0, 99.4, 102.8, 99.2, 102.2, _AVERAGE_VOLUME])

    assert verdict.entry_status is EntryStatus.NOT_READY
    assert _row(verdict, CONDITION_BREAK_VOLUME).met is False
    assert "did not expand" in _row(verdict, CONDITION_BREAK_VOLUME).detail


def test_a_close_barely_past_the_level_is_a_touch_not_a_break() -> None:
    verdict = _evaluate([20000.0, 99.4, 100.4, 99.2, 100.2, _EXPANDED_VOLUME])

    assert _row(verdict, CONDITION_BREAK).met is False
    assert "rather than a touch" in _row(verdict, CONDITION_BREAK).detail


def test_a_mostly_wick_breaking_candle_fails_the_candle_condition() -> None:
    """Closes clear of the level, but left most of its range above as rejection."""

    verdict = _evaluate([20000.0, 101.9, 108.0, 101.8, 102.2, _EXPANDED_VOLUME])

    assert _row(verdict, CONDITION_BREAK_CANDLE).met is False


def test_structure_pointing_the_other_way_argues_against_continuation() -> None:
    verdict = _evaluate(
        [20000.0, 99.4, 102.8, 99.2, 102.2, _EXPANDED_VOLUME],
        structure=_structure(Structure.BEARISH),
    )

    assert _row(verdict, CONDITION_CONTINUATION).met is False
    assert "argues against continuation" in _row(verdict, CONDITION_CONTINUATION).detail


def test_a_broken_structure_does_not_block_a_breakout() -> None:
    """Structure is context here, not a gate — a range break ends a sideways structure."""

    verdict = _evaluate(
        [20000.0, 99.4, 102.8, 99.2, 102.2, _EXPANDED_VOLUME],
        structure=_structure(Structure.BROKEN),
    )

    assert _row(verdict, CONDITION_CONTINUATION).met is True


def test_a_level_just_overhead_leaves_no_room_for_the_trade() -> None:
    verdict = _evaluate(
        [20000.0, 99.4, 102.8, 99.2, 102.2, _EXPANDED_VOLUME],
        structure=_structure(highs=(99.9, 100.1, 100.0, 105.0, 105.1)),
    )

    assert _row(verdict, CONDITION_ROOM).met is False
    assert "next level" in _row(verdict, CONDITION_ROOM).detail


# --- The short mirror --------------------------------------------------------------


def test_a_support_break_is_a_short_and_reads_2b() -> None:
    rows = [
        [float(i) * 1000.0, 96.0, 96.5, 95.2, 95.8, _AVERAGE_VOLUME] for i in range(20)
    ]
    rows.append([20000.0, 95.6, 95.7, 91.0, 91.4, _EXPANDED_VOLUME])
    verdict = evaluate_breakout(
        "AAA",
        _candles(rows),
        _features(),
        _structure(Structure.BEARISH, highs=(120.0, 120.2), lows=(95.0, 95.1, 94.9)),
    )

    assert verdict.strategy is StrategyId.BREAKOUT_SHORT
    assert verdict.decision is Direction.SHORT
    assert verdict.entry_status is EntryStatus.READY


# --- Degenerate input --------------------------------------------------------------


def test_a_coin_with_no_established_level_matches_no_setup() -> None:
    """Not WAIT: a coin with nothing to break is not waiting for a breakout entry."""

    verdict = evaluate_breakout(
        "AAA", _candles(_history()), _features(), _structure(highs=(100.0,), lows=(94.0,))
    )

    assert verdict.strategy is None
    assert verdict.trend is Direction.NONE
    assert verdict.entry_status is EntryStatus.NOT_READY
    assert _row(verdict, CONDITION_LEVEL).met is False


def test_no_candles_is_unjudgeable_rather_than_failed() -> None:
    empty = Candles(symbol="AAA", timeframe="4h", frame=pd.DataFrame(columns=OHLCV_COLUMNS))

    verdict = evaluate_breakout("AAA", empty, _features(), _structure())

    assert verdict.strategy is None
    assert _row(verdict, CONDITION_LEVEL).met is None


def test_an_unreadable_atr_is_unjudgeable() -> None:
    verdict = evaluate_breakout(
        "AAA", _candles(_history()), _features(atr=float("nan")), _structure()
    )

    assert verdict.strategy is None
    assert _row(verdict, CONDITION_LEVEL).met is None

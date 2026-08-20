"""Tests for the named-strategy checklist and its two-question verdict.

The evaluator reads computed facts rather than candles, which is what lets every row of
the checklist be flipped independently: each test below changes exactly one condition and
asserts the verdict moves as the method says it should. No price series is constructed.

Two properties matter more than the arithmetic and are asserted directly. **WAIT must be
reachable and must explain itself** — every unmet condition names a measurable quantity
with its value, because a blocker the trader cannot check against their own chart is one
they cannot learn from. And **a coin with no trend matches no setup**, rather than being
reported as waiting for an entry that can never arrive.
"""

from __future__ import annotations

import math

import pytest

from trader.direction import Direction
from trader.indicators import TimeframeFeatures
from trader.patterns import Pattern
from trader.strategy import (
    CONDITION_MACD,
    CONDITION_MOMENTUM_TURN,
    CONDITION_REACTION,
    CONDITION_STACK,
    CONDITION_STRUCTURE,
    CONDITION_TRENDLINE,
    CONDITION_ZONE,
    EntryStatus,
    StrategyId,
    evaluate,
    stack_direction,
)
from trader.structure import Structure, StructureState
from trader.trendline import Trendline

ATR = 2.0


def _features(
    *,
    ema10: float = 104.0,
    ema21: float = 102.0,
    ema50: float = 98.0,
    sma200: float = 90.0,
    atr: float = ATR,
    stoch_rsi_k: float = 0.30,
    stoch_rsi_d: float = 0.25,
    macd: float = 1.0,
    macd_signal: float = 0.5,
    macd_crossed_up: bool = False,
    macd_crossed_down: bool = False,
    macd_bars_ago: int | None = None,
    crossed_up: bool = True,
    crossed_down: bool = False,
    cross_bars_ago: int | None = 1,
    cross_extreme: float = 0.10,
) -> TimeframeFeatures:
    """A bullish-stacked bundle with a satisfied momentum turn; tests perturb one value.

    The cross fields are set explicitly rather than derived from a price series: the point
    of the evaluator reading facts is that a checklist row can be flipped on its own. The
    *derivation* of those facts from candles is covered in test_indicators.py.
    """

    return TimeframeFeatures(
        symbol="AAA",
        timeframe="4h",
        ema10=ema10,
        ema21=ema21,
        ema50=ema50,
        sma200=sma200,
        macd=macd,
        macd_signal=macd_signal,
        macd_hist=0.5,
        macd_crossed_up=macd_crossed_up,
        macd_crossed_down=macd_crossed_down,
        macd_cross_bars_ago=macd_bars_ago,
        atr=atr,
        volume=1_000.0,
        relative_volume=1.2,
        stoch_rsi_k=stoch_rsi_k,
        stoch_rsi_d=stoch_rsi_d,
        stoch_crossed_up=crossed_up,
        stoch_crossed_down=crossed_down,
        stoch_cross_bars_ago=cross_bars_ago,
        stoch_cross_extreme=cross_extreme,
    )


def _bearish_features(**overrides: object) -> TimeframeFeatures:
    """The mirror: EMA10 < EMA21 < EMA50 < SMA200, with a downward turn from overbought."""

    base: dict[str, object] = {
        "ema10": 90.0,
        "ema21": 94.0,
        "ema50": 98.0,
        "sma200": 110.0,
        "stoch_rsi_k": 0.70,
        "stoch_rsi_d": 0.75,
        "crossed_up": False,
        "crossed_down": True,
        "cross_extreme": 0.90,
        "macd": -1.0,
        "macd_signal": -0.5,
    }
    base.update(overrides)
    return _features(**base)  # type: ignore[arg-type]


# The reaction the checklist wants, supplied by default so a test about (say) the stack is
# not silently also a test about candlesticks.
_BULL_REACTION = (Pattern.HAMMER,)
_BEAR_REACTION = (Pattern.SHOOTING_STAR,)


def _line(trend: Direction, close: float) -> Trendline:
    """A trendline the trade is sitting on: rising just under a long, falling just over a
    short. Tracks the close so it stays satisfied whatever price the test chose."""

    return Trendline(
        slope=1.0 if trend is Direction.LONG else -1.0,
        level_now=close - 0.5 if trend is Direction.LONG else close + 0.5,
        touches=4,
        r_squared=0.95,
    )


def _evaluate(  # type: ignore[no-untyped-def]
    symbol, trend, features, structure, close, *, patterns=None, trendline=..., **kwargs
):
    """Call the evaluator with the reaction and trendline satisfied unless supplied.

    Without this every test would also be a test about candlesticks and lines: a checklist
    row left unsatisfied by omission would fail the tests about the *other* rows, and the
    failure would point at the wrong condition.
    """

    if patterns is None:
        patterns = _BULL_REACTION if trend is Direction.LONG else _BEAR_REACTION
    if trendline is ...:
        trendline = _line(trend, close)
    return evaluate(
        symbol, trend, features, structure, close,
        patterns=patterns, trendline=trendline, **kwargs,
    )


def _structure(state: Structure = Structure.BULLISH) -> StructureState:
    return StructureState(structure=state, swing_highs=(120.0,), swing_lows=(80.0,))


# --- The stack, read the method's way ---------------------------------------------


def test_the_stack_direction_follows_the_methods_four_line_ordering() -> None:
    assert stack_direction(_features()) is Direction.LONG
    assert stack_direction(_bearish_features()) is Direction.SHORT
    # One line out of order is not a stack.
    assert stack_direction(_features(ema10=101.0)) is Direction.NONE


def test_an_absent_long_term_average_yields_no_stack_direction() -> None:
    # Not a failure of ordering — there is simply no filter to order against.
    assert stack_direction(_features(sma200=math.nan)) is Direction.NONE


# --- The full-house case ----------------------------------------------------------


def test_a_complete_checklist_is_ready_and_decides_the_trade() -> None:
    verdict = _evaluate("AAA", Direction.LONG, _features(), _structure(), close=100.0)

    assert verdict.trend is Direction.LONG
    assert verdict.strategy is StrategyId.TREND_PULLBACK_LONG
    assert verdict.entry_status is EntryStatus.READY
    assert verdict.decision is Direction.LONG
    assert verdict.conditions_met == 7


def test_the_short_mirror_is_ready_on_the_same_terms() -> None:
    verdict = _evaluate(
        "AAA",
        Direction.SHORT,
        _bearish_features(),
        _structure(Structure.BEARISH),
        close=96.0,
    )

    assert verdict.trend is Direction.SHORT
    assert verdict.strategy is StrategyId.TREND_PULLBACK_SHORT
    assert verdict.entry_status is EntryStatus.READY
    assert verdict.decision is Direction.SHORT


# --- The case the method exists to refuse -----------------------------------------


def test_an_extended_long_waits_and_names_the_distance_and_the_zone() -> None:
    # Price 5 ATR above the band: the move happened without us.
    verdict = _evaluate(
        "AAA", Direction.LONG, _features(), _structure(), close=112.0, max_extension_atr=2.0
    )

    assert verdict.trend is Direction.LONG
    assert verdict.strategy is StrategyId.TREND_PULLBACK_LONG
    assert verdict.entry_status is EntryStatus.NOT_READY
    assert verdict.decision is Direction.NONE
    assert "5.0 ATR above" in verdict.reason
    assert "98-102" in verdict.reason
    assert "limit 2.0" in verdict.reason


def test_an_extended_short_waits_and_refers_to_resistance_not_support() -> None:
    verdict = _evaluate(
        "AAA",
        Direction.SHORT,
        _bearish_features(),
        _structure(Structure.BEARISH),
        close=84.0,
        max_extension_atr=2.0,
    )

    assert verdict.entry_status is EntryStatus.NOT_READY
    assert verdict.decision is Direction.NONE
    assert "below" in verdict.reason
    assert "resistance" in verdict.reason
    assert "support" not in verdict.reason


def test_the_same_coin_inside_the_band_is_actionable() -> None:
    """The single variable is distance from the zone."""
    extended = _evaluate("AAA", Direction.LONG, _features(), _structure(), close=112.0)
    pulled_back = _evaluate("AAA", Direction.LONG, _features(), _structure(), close=100.0)

    assert extended.entry_status is EntryStatus.NOT_READY
    assert pulled_back.entry_status is EntryStatus.READY


def test_raising_the_extension_limit_admits_a_previously_extended_coin() -> None:
    strict = _evaluate(
        "AAA", Direction.LONG, _features(), _structure(), close=108.0, max_extension_atr=2.0
    )
    loose = _evaluate(
        "AAA", Direction.LONG, _features(), _structure(), close=108.0, max_extension_atr=6.0
    )

    assert strict.entry_status is EntryStatus.NOT_READY
    assert loose.entry_status is EntryStatus.READY


def test_a_long_below_the_zone_is_not_treated_as_extended() -> None:
    """Overshooting the pullback is a different thing from chasing the move.

    Distance is read in the trade's direction, so a long *below* the band has gone the
    wrong way rather than run away, and an absolute distance would conflate the two.
    """

    verdict = _evaluate("AAA", Direction.LONG, _features(), _structure(), close=92.0)

    assert verdict.entry_status is EntryStatus.READY
    zone = verdict.condition(CONDITION_ZONE)
    assert zone is not None and "pullback side" in zone.detail


# --- No trend, no setup -----------------------------------------------------------


def test_a_coin_whose_stack_fails_matches_no_setup_at_all() -> None:
    verdict = _evaluate("AAA", Direction.LONG, _features(ema10=101.0), _structure(), close=100.0)

    assert verdict.strategy is None
    assert verdict.trend is Direction.NONE
    assert verdict.decision is Direction.NONE
    stack = verdict.condition(CONDITION_STACK)
    assert stack is not None and stack.met is False


def test_a_coin_whose_structure_opposes_matches_no_setup() -> None:
    verdict = _evaluate(
        "AAA", Direction.LONG, _features(), _structure(Structure.BEARISH), close=100.0
    )

    assert verdict.strategy is None
    structure = verdict.condition(CONDITION_STRUCTURE)
    assert structure is not None and structure.met is False


def test_a_directionless_coin_matches_no_setup() -> None:
    verdict = _evaluate("AAA", Direction.NONE, _features(), _structure(), close=100.0)

    assert verdict.strategy is None
    assert verdict.entry_status is EntryStatus.NOT_READY


def test_an_unjudgeable_stack_is_distinguished_from_a_failed_one() -> None:
    """Absent data and a wrong answer are different facts and must read differently."""

    verdict = _evaluate(
        "AAA", Direction.LONG, _features(sma200=math.nan), _structure(), close=100.0
    )

    stack = verdict.condition(CONDITION_STACK)
    assert stack is not None
    assert stack.met is None
    assert "too little history" in stack.detail


# --- What a WAIT must always carry ------------------------------------------------


@pytest.mark.parametrize(
    "close,features,structure",
    [
        (112.0, _features(), _structure()),
        (100.0, _features(ema10=101.0), _structure()),
        (100.0, _features(), _structure(Structure.BROKEN)),
        (100.0, _features(sma200=math.nan), _structure()),
    ],
)
def test_every_unmet_verdict_explains_itself_with_a_measured_value(
    close: float, features: TimeframeFeatures, structure: StructureState
) -> None:
    import re

    verdict = _evaluate("AAA", Direction.LONG, features, structure, close=close)

    assert verdict.entry_status is EntryStatus.NOT_READY
    assert verdict.reason
    # Every reason quotes at least one number the trader can check on their own chart.
    assert re.search(r"\d", verdict.reason), verdict.reason


def test_the_reason_is_derived_from_the_checklist_and_cannot_disagree_with_it() -> None:
    verdict = _evaluate("AAA", Direction.LONG, _features(), _structure(), close=112.0)

    unmet = [c for c in verdict.conditions if c.met is False]
    assert unmet
    for condition in unmet:
        assert condition.detail in verdict.reason


def test_a_partial_checklist_reports_how_many_conditions_it_judged() -> None:
    """A READY on three of seven must not imply the whole method was applied."""

    verdict = _evaluate("AAA", Direction.LONG, _features(), _structure(), close=100.0)

    assert verdict.conditions_evaluated == 7
    assert verdict.conditions_met == 7
    assert len(verdict.conditions) == 7


# --- The momentum turn: an event, from an extreme ---------------------------------


def test_a_fresh_cross_up_from_oversold_satisfies_the_turn() -> None:
    verdict = _evaluate("AAA", Direction.LONG, _features(), _structure(), close=100.0)

    turn = verdict.condition(CONDITION_MOMENTUM_TURN)
    assert turn is not None and turn.met is True
    assert verdict.entry_status is EntryStatus.READY
    assert verdict.decision is Direction.LONG


def test_deeply_oversold_without_a_cross_is_not_a_turn() -> None:
    """A falling knife is oversold the whole way down; the level alone is not a signal."""

    verdict = _evaluate(
        "AAA",
        Direction.LONG,
        _features(stoch_rsi_k=0.05, stoch_rsi_d=0.09, crossed_up=False, cross_bars_ago=None),
        _structure(),
        close=100.0,
    )

    assert verdict.entry_status is EntryStatus.NOT_READY
    assert "has not crossed" in verdict.reason
    # The reason quotes both readings so the trader can check them.
    assert "0.05" in verdict.reason and "0.09" in verdict.reason


def test_a_cross_from_mid_range_is_not_a_turn_from_an_extreme() -> None:
    verdict = _evaluate(
        "AAA", Direction.LONG, _features(cross_extreme=0.55), _structure(), close=100.0
    )

    assert verdict.entry_status is EntryStatus.NOT_READY
    assert "not from an extreme" in verdict.reason
    assert "0.55" in verdict.reason


def test_a_stale_cross_no_longer_counts_as_a_turn() -> None:
    fresh = _evaluate(
        "AAA", Direction.LONG, _features(cross_bars_ago=3), _structure(), close=100.0,
        cross_lookback=3,
    )
    stale = _evaluate(
        "AAA", Direction.LONG, _features(cross_bars_ago=4), _structure(), close=100.0,
        cross_lookback=3,
    )

    assert fresh.entry_status is EntryStatus.READY
    assert stale.entry_status is EntryStatus.NOT_READY
    assert "stale" in stale.reason
    assert "4 bars ago" in stale.reason


def test_the_short_side_needs_a_cross_down_from_overbought() -> None:
    verdict = _evaluate(
        "AAA",
        Direction.SHORT,
        _bearish_features(),
        _structure(Structure.BEARISH),
        close=96.0,
    )

    turn = verdict.condition(CONDITION_MOMENTUM_TURN)
    assert turn is not None and turn.met is True
    assert "below" in turn.detail


def test_a_short_with_an_upward_cross_is_not_turning_its_way() -> None:
    verdict = _evaluate(
        "AAA",
        Direction.SHORT,
        _bearish_features(crossed_up=True, crossed_down=False, cross_extreme=0.10),
        _structure(Structure.BEARISH),
        close=96.0,
    )

    assert verdict.entry_status is EntryStatus.NOT_READY
    assert "has not crossed below" in verdict.reason


def test_changing_the_oversold_threshold_changes_the_outcome() -> None:
    features = _features(cross_extreme=0.30)

    strict = _evaluate(
        "AAA", Direction.LONG, features, _structure(), close=100.0, stoch_oversold=0.20
    )
    lenient = _evaluate(
        "AAA", Direction.LONG, features, _structure(), close=100.0, stoch_oversold=0.35
    )

    assert strict.entry_status is EntryStatus.NOT_READY
    assert lenient.entry_status is EntryStatus.READY


def test_an_absent_stochastic_rsi_is_unjudgeable_rather_than_failed() -> None:
    verdict = _evaluate(
        "AAA",
        Direction.LONG,
        _features(stoch_rsi_k=math.nan, stoch_rsi_d=math.nan),
        _structure(),
        close=100.0,
    )

    turn = verdict.condition(CONDITION_MOMENTUM_TURN)
    assert turn is not None and turn.met is None
    # Unjudgeable is not a pass: READY requires every entry condition positively met.
    assert verdict.entry_status is EntryStatus.NOT_READY


# --- MACD: a crossover and/or the right side of zero ------------------------------


def test_a_fresh_crossover_satisfies_macd_and_names_the_side_of_zero() -> None:
    verdict = _evaluate(
        "AAA",
        Direction.LONG,
        _features(macd=0.4, macd_signal=0.3, macd_crossed_up=True, macd_bars_ago=1),
        _structure(),
        close=100.0,
    )

    macd = verdict.condition(CONDITION_MACD)
    assert macd is not None and macd.met is True
    assert "crossed above signal 1 bars ago" in macd.detail
    assert "above zero" in macd.detail
    assert "both confirm" in macd.detail


def test_a_crossover_below_zero_still_satisfies_but_says_so() -> None:
    """A turn in negative territory is weaker evidence and must read differently."""

    verdict = _evaluate(
        "AAA",
        Direction.LONG,
        _features(macd=-0.2, macd_signal=-0.3, macd_crossed_up=True, macd_bars_ago=1),
        _structure(),
        close=100.0,
    )

    macd = verdict.condition(CONDITION_MACD)
    assert macd is not None and macd.met is True
    assert "though still below zero" in macd.detail
    assert "both confirm" not in macd.detail


def test_a_long_standing_bullish_macd_satisfies_on_the_zero_line_alone() -> None:
    """No fresh crossover, but the sign of momentum still confirms."""

    verdict = _evaluate(
        "AAA",
        Direction.LONG,
        _features(macd=0.8, macd_signal=0.6, macd_crossed_up=True, macd_bars_ago=14),
        _structure(),
        close=100.0,
        cross_lookback=3,
    )

    macd = verdict.condition(CONDITION_MACD)
    assert macd is not None and macd.met is True
    assert "above zero" in macd.detail
    assert "last cross 14 bars ago" in macd.detail
    assert "crossed above signal 14" not in macd.detail


def test_macd_below_both_its_signal_and_zero_fails_a_long() -> None:
    verdict = _evaluate(
        "AAA",
        Direction.LONG,
        _features(macd=-0.4, macd_signal=-0.1, macd_crossed_down=True, macd_bars_ago=1),
        _structure(),
        close=100.0,
    )

    macd = verdict.condition(CONDITION_MACD)
    assert macd is not None and macd.met is False
    # Both readings, with their values.
    assert "no above crossover" in macd.detail
    assert "below zero" in macd.detail
    assert "-0.4" in macd.detail and "-0.1" in macd.detail
    assert verdict.entry_status is EntryStatus.NOT_READY


def test_the_short_side_is_satisfied_by_a_bearish_crossover() -> None:
    verdict = _evaluate(
        "AAA",
        Direction.SHORT,
        _bearish_features(macd=0.3, macd_signal=0.5, macd_crossed_down=True, macd_bars_ago=1),
        _structure(Structure.BEARISH),
        close=96.0,
    )

    macd = verdict.condition(CONDITION_MACD)
    assert macd is not None and macd.met is True
    assert "crossed below signal" in macd.detail


def test_the_short_side_is_satisfied_by_sitting_below_zero() -> None:
    verdict = _evaluate(
        "AAA",
        Direction.SHORT,
        _bearish_features(macd=-0.7, macd_signal=-0.9),
        _structure(Structure.BEARISH),
        close=96.0,
    )

    macd = verdict.condition(CONDITION_MACD)
    assert macd is not None and macd.met is True
    assert "below zero" in macd.detail


def test_a_stale_crossover_does_not_count_but_the_zero_line_may_still_carry_it() -> None:
    stale_and_wrong_side = _evaluate(
        "AAA",
        Direction.LONG,
        _features(macd=-0.3, macd_signal=-0.4, macd_crossed_up=True, macd_bars_ago=9),
        _structure(),
        close=100.0,
        cross_lookback=3,
    )

    assert stale_and_wrong_side.condition(CONDITION_MACD).met is False  # type: ignore[union-attr]
    assert stale_and_wrong_side.entry_status is EntryStatus.NOT_READY


def test_an_absent_macd_is_unjudgeable_rather_than_failed() -> None:
    verdict = _evaluate(
        "AAA",
        Direction.LONG,
        _features(macd=math.nan, macd_signal=math.nan),
        _structure(),
        close=100.0,
    )

    macd = verdict.condition(CONDITION_MACD)
    assert macd is not None and macd.met is None
    assert verdict.entry_status is EntryStatus.NOT_READY


# --- The reaction candle: positional, not merely present --------------------------


def test_a_bullish_reaction_at_the_zone_satisfies_the_condition() -> None:
    verdict = _evaluate(
        "AAA", Direction.LONG, _features(), _structure(), 100.0,
        patterns=(Pattern.BULLISH_ENGULFING,),
    )

    reaction = verdict.condition(CONDITION_REACTION)
    assert reaction is not None and reaction.met is True
    assert "bullish_engulfing" in reaction.detail
    assert verdict.entry_status is EntryStatus.READY


def test_no_reaction_leaves_the_entry_waiting_and_names_the_condition() -> None:
    verdict = _evaluate("AAA", Direction.LONG, _features(), _structure(), 100.0, patterns=())

    reaction = verdict.condition(CONDITION_REACTION)
    assert reaction is not None and reaction.met is False
    assert "no bullish reaction candle" in verdict.reason
    assert verdict.entry_status is EntryStatus.NOT_READY


def test_a_bullish_reaction_away_from_the_zone_does_not_count() -> None:
    """A reaction somewhere else is a reaction to something else."""

    verdict = _evaluate(
        "AAA", Direction.LONG, _features(), _structure(), 112.0,
        patterns=(Pattern.HAMMER,), max_extension_atr=2.0,
    )

    reaction = verdict.condition(CONDITION_REACTION)
    assert reaction is not None and reaction.met is False
    assert "not at the zone" in reaction.detail


def test_a_long_is_not_satisfied_by_a_bearish_reaction() -> None:
    verdict = _evaluate(
        "AAA", Direction.LONG, _features(), _structure(), 100.0,
        patterns=(Pattern.SHOOTING_STAR,),
    )

    reaction = verdict.condition(CONDITION_REACTION)
    assert reaction is not None and reaction.met is False
    # The reason says what *was* found, so the trader can see the disagreement.
    assert "shooting_star" in reaction.detail


def test_a_short_is_satisfied_only_by_a_bearish_reaction() -> None:
    bearish = _evaluate(
        "AAA", Direction.SHORT, _bearish_features(), _structure(Structure.BEARISH), 96.0,
        patterns=(Pattern.EVENING_STAR,),
    )
    bullish = _evaluate(
        "AAA", Direction.SHORT, _bearish_features(), _structure(Structure.BEARISH), 96.0,
        patterns=(Pattern.HAMMER,),
    )

    assert bearish.condition(CONDITION_REACTION).met is True  # type: ignore[union-attr]
    assert bullish.condition(CONDITION_REACTION).met is False  # type: ignore[union-attr]


def test_no_candles_supplied_makes_the_reaction_unjudgeable() -> None:
    verdict = _evaluate(
        "AAA", Direction.LONG, _features(), _structure(), 100.0, patterns=None
    )
    # ``_evaluate`` substitutes a default, so call the evaluator directly for this one.
    direct = evaluate("AAA", Direction.LONG, _features(), _structure(), 100.0)

    assert verdict.condition(CONDITION_REACTION).met is True  # type: ignore[union-attr]
    assert direct.condition(CONDITION_REACTION).met is None  # type: ignore[union-attr]
    assert direct.entry_status is EntryStatus.NOT_READY


# --- The trendline: a directional line, and price returned to it ------------------


def test_price_on_its_rising_trendline_satisfies_the_condition() -> None:
    verdict = _evaluate("AAA", Direction.LONG, _features(), _structure(), 100.0)

    line = verdict.condition(CONDITION_TRENDLINE)
    assert line is not None and line.met is True
    assert "rising trendline" in line.detail
    assert verdict.entry_status is EntryStatus.READY


def test_price_far_above_its_rising_trendline_has_not_returned_to_it() -> None:
    verdict = _evaluate(
        "AAA", Direction.LONG, _features(), _structure(), 100.0,
        trendline=Trendline(slope=1.0, level_now=88.0, touches=4, r_squared=0.95),
        trendline_tolerance_atr=1.5,
    )

    line = verdict.condition(CONDITION_TRENDLINE)
    assert line is not None and line.met is False
    assert "6.0 ATR above the rising trendline at 88" in line.detail
    assert "wait for a return to that support" in line.detail
    assert verdict.entry_status is EntryStatus.NOT_READY


def test_a_line_sloping_the_wrong_way_does_not_satisfy_a_long() -> None:
    verdict = _evaluate(
        "AAA", Direction.LONG, _features(), _structure(), 100.0,
        trendline=Trendline(slope=-1.0, level_now=99.5, touches=4, r_squared=0.95),
    )

    line = verdict.condition(CONDITION_TRENDLINE)
    assert line is not None and line.met is False
    assert "trendline is falling, not rising" in line.detail


def test_a_short_needs_a_falling_line_and_a_rally_back_to_it() -> None:
    at_line = _evaluate(
        "AAA", Direction.SHORT, _bearish_features(), _structure(Structure.BEARISH), 96.0
    )
    far_below = _evaluate(
        "AAA", Direction.SHORT, _bearish_features(), _structure(Structure.BEARISH), 96.0,
        trendline=Trendline(slope=-1.0, level_now=108.0, touches=4, r_squared=0.95),
        trendline_tolerance_atr=1.5,
    )

    assert at_line.condition(CONDITION_TRENDLINE).met is True  # type: ignore[union-attr]
    assert far_below.condition(CONDITION_TRENDLINE).met is False  # type: ignore[union-attr]
    assert "wait for a return to that resistance" in far_below.reason


def test_no_trendline_is_unjudgeable_rather_than_failed() -> None:
    """Too few pivots is missing evidence, not evidence against."""

    verdict = _evaluate(
        "AAA", Direction.LONG, _features(), _structure(), 100.0, trendline=None
    )

    line = verdict.condition(CONDITION_TRENDLINE)
    assert line is not None and line.met is None
    assert "too few swing pivots" in line.detail
    # Unjudgeable is not a pass.
    assert verdict.entry_status is EntryStatus.NOT_READY


def test_widening_the_tolerance_admits_a_line_price_had_drifted_from() -> None:
    line = Trendline(slope=1.0, level_now=94.0, touches=4, r_squared=0.95)

    strict = _evaluate(
        "AAA", Direction.LONG, _features(), _structure(), 100.0,
        trendline=line, trendline_tolerance_atr=1.5,
    )
    loose = _evaluate(
        "AAA", Direction.LONG, _features(), _structure(), 100.0,
        trendline=line, trendline_tolerance_atr=6.0,
    )

    assert strict.condition(CONDITION_TRENDLINE).met is False  # type: ignore[union-attr]
    assert loose.condition(CONDITION_TRENDLINE).met is True  # type: ignore[union-attr]


def test_the_detail_reports_how_good_the_fit_was() -> None:
    verdict = _evaluate(
        "AAA", Direction.LONG, _features(), _structure(), 100.0,
        trendline=Trendline(slope=1.0, level_now=99.5, touches=5, r_squared=0.88),
    )

    line = verdict.condition(CONDITION_TRENDLINE)
    assert line is not None
    assert "5 touches" in line.detail
    assert "fit 0.88" in line.detail

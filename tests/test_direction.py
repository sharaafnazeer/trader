"""Unit tests for the higher-timeframe direction decision (pure, deterministic).

Features and structure are hand-built directly so each branch of the lead-timeframe
rule (lead resolves, filter opposes, filter unconfirmed, BTC veto, lead unresolved)
and each reason string is pinned by its inputs, mirroring the boundary-focused style
of the v1 ranking tests. The lead timeframe is ``1d`` and the filter is ``4h``.
"""

from __future__ import annotations

from trader.direction import (
    Direction,
    DirectionResult,
    MarketContext,
    _timeframe_direction,
    decide,
)
from trader.indicators import TimeframeFeatures
from trader.structure import Structure, StructureState

_STACKS = {
    "long": (30.0, 20.0, 10.0),
    "short": (10.0, 20.0, 30.0),
    "neutral": (20.0, 20.0, 20.0),  # equal EMAs -> no stack -> no direction
}


def _features(stack: str, timeframe: str = "1d") -> TimeframeFeatures:
    """Features with a cleanly stacked, inverted, or neutral EMA 20/50/200 ordering."""
    ema20, ema50, ema200 = _STACKS[stack]
    return TimeframeFeatures(
        symbol="X",
        timeframe=timeframe,
        ema10=ema20 + (ema20 - ema50) * 0.1,
        ema21=ema20,
        ema50=ema50,
        sma200=ema200,
        macd=0.0,
        macd_signal=0.0,
        macd_hist=0.0,
        atr=1.0,
        volume=1_000.0,
        relative_volume=1.0,
    )


def _structure(kind: str) -> StructureState:
    """A bullish, bearish, or broken (non-committal) structure state."""
    if kind == "bullish":
        return StructureState(
            structure=Structure.BULLISH, swing_highs=(1.0, 2.0), swing_lows=(0.5, 1.0)
        )
    if kind == "bearish":
        return StructureState(
            structure=Structure.BEARISH, swing_highs=(2.0, 1.0), swing_lows=(1.0, 0.5)
        )
    return StructureState(structure=Structure.BROKEN, swing_highs=(), swing_lows=())


def _maps(
    lead_stack: str,
    filter_stack: str,
    lead_struct: str = "broken",
    filter_struct: str = "broken",
):
    """Feature/structure maps for the ``1d`` lead and ``4h`` filter timeframes."""
    features = {
        "1d": _features(lead_stack, "1d"),
        "4h": _features(filter_stack, "4h"),
    }
    structure = {
        "1d": _structure(lead_struct),
        "4h": _structure(filter_struct),
    }
    return features, structure


# --- _timeframe_direction: EMA stack drives; structure only vetoes when opposite ----


def test_timeframe_direction_stack_drives_with_broken_structure() -> None:
    # A clean bullish stack with BROKEN structure still resolves LONG (not vetoed).
    assert _timeframe_direction(_features("long"), _structure("broken")) is Direction.LONG
    assert _timeframe_direction(_features("short"), _structure("broken")) is Direction.SHORT


def test_timeframe_direction_opposing_structure_vetoes() -> None:
    # Opposing structure vetoes the stack; agreeing/neutral structure does not.
    assert _timeframe_direction(_features("long"), _structure("bearish")) is Direction.NONE
    assert _timeframe_direction(_features("short"), _structure("bullish")) is Direction.NONE
    assert _timeframe_direction(_features("long"), _structure("bullish")) is Direction.LONG


def test_timeframe_direction_neutral_stack_is_none() -> None:
    assert _timeframe_direction(_features("neutral"), _structure("bullish")) is Direction.NONE


# --- decide: lead resolves, filter must-not-oppose (default) -------------------------


def test_lead_long_with_neutral_filter_is_long() -> None:
    features, structure = _maps(lead_stack="long", filter_stack="neutral")
    assert decide(features, structure, MarketContext()) == DirectionResult(Direction.LONG, None)


def test_lead_short_with_neutral_filter_is_short() -> None:
    features, structure = _maps(lead_stack="short", filter_stack="neutral")
    assert decide(features, structure, MarketContext()) == DirectionResult(Direction.SHORT, None)


def test_lead_with_broken_structure_still_resolves_direction() -> None:
    # Clean EMA stack on the lead but BROKEN structure -> direction still resolved.
    features, structure = _maps(
        lead_stack="long", filter_stack="neutral", lead_struct="broken"
    )
    result = decide(features, structure, MarketContext())
    assert result.direction is Direction.LONG
    assert result.reason is None


def test_filter_opposing_the_lead_is_none_with_filter_opposed() -> None:
    features, structure = _maps(lead_stack="long", filter_stack="short")
    result = decide(features, structure, MarketContext())
    assert result == DirectionResult(Direction.NONE, "filter_opposed")


def test_neutral_filter_allowed_by_default_but_blocked_under_require_confirmation() -> None:
    features, structure = _maps(lead_stack="long", filter_stack="neutral")
    # Default (must-not-oppose): neutral filter is fine.
    assert decide(features, structure, MarketContext()).direction is Direction.LONG
    # require_confirmation (must-confirm): a merely-neutral filter blocks the trade.
    blocked = decide(features, structure, MarketContext(), require_confirmation=True)
    assert blocked == DirectionResult(Direction.NONE, "filter_unconfirmed")


def test_confirming_filter_surfaces_under_require_confirmation() -> None:
    features, structure = _maps(lead_stack="long", filter_stack="long")
    result = decide(features, structure, MarketContext(), require_confirmation=True)
    assert result == DirectionResult(Direction.LONG, None)


# --- decide: BTC veto ----------------------------------------------------------------


def test_opposing_btc_regime_vetoes_with_btc_veto_reason() -> None:
    features, structure = _maps(lead_stack="long", filter_stack="neutral")
    context = MarketContext(btc_direction=Direction.SHORT)
    assert decide(features, structure, context) == DirectionResult(Direction.NONE, "btc_veto")


def test_btc_veto_off_does_not_veto() -> None:
    features, structure = _maps(lead_stack="long", filter_stack="neutral")
    context = MarketContext(btc_direction=Direction.SHORT)
    result = decide(features, structure, context, btc_veto=False)
    assert result == DirectionResult(Direction.LONG, None)


def test_aligned_btc_context_does_not_veto() -> None:
    features, structure = _maps(lead_stack="long", filter_stack="neutral")
    context = MarketContext(btc_direction=Direction.LONG)
    assert decide(features, structure, context).direction is Direction.LONG


# --- decide: lead unresolved ---------------------------------------------------------


def test_neutral_lead_is_none_with_lead_unresolved() -> None:
    features, structure = _maps(lead_stack="neutral", filter_stack="long")
    assert decide(features, structure, MarketContext()) == DirectionResult(
        Direction.NONE, "lead_unresolved"
    )


def test_missing_lead_timeframe_is_lead_unresolved() -> None:
    features, structure = _maps(lead_stack="long", filter_stack="long")
    del features["1d"]
    assert decide(features, structure, MarketContext()) == DirectionResult(
        Direction.NONE, "lead_unresolved"
    )


def test_lead_stack_opposed_by_structure_is_lead_unresolved() -> None:
    # Bullish stack on the lead but BEARISH structure vetoes it -> lead has no direction.
    features, structure = _maps(
        lead_stack="long", filter_stack="neutral", lead_struct="bearish"
    )
    assert decide(features, structure, MarketContext()) == DirectionResult(
        Direction.NONE, "lead_unresolved"
    )

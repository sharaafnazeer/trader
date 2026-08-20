"""Tests for the analyst vocabulary and, above all, verdict validation.

Validation is the gate that stops a confident, well-written, impossible trade from being
shown to a trader as something to act on, so every rejection path is pinned here.
"""

from __future__ import annotations

import pytest

from trader.analyst import (
    Action,
    AnalystReview,
    AnalystVerdict,
    agrees_with_engine,
    partition_verdicts,
    validate_verdict,
)
from trader.brief import LiquidityBrief, SetupBrief
from trader.direction import Direction


def _brief(symbol: str = "SOLUSDT", direction: Direction = Direction.LONG) -> SetupBrief:
    return SetupBrief(
        symbol=symbol,
        direction=direction,
        total=82.0,
        categories=(),
        timeframes=(),
        plan=None,
        liquidity=LiquidityBrief(
            spread_pct=0.1, bid_depth_notional=50_000.0, ask_depth_notional=50_000.0
        ),
        limited_history=False,
    )


def _long(**overrides: object) -> AnalystVerdict:
    base: dict[str, object] = {
        "symbol": "SOLUSDT",
        "action": Action.LONG,
        "confidence": 72.0,
        "entry": 100.0,
        "stop_loss": 94.0,
        "take_profits": (112.0, 120.0),
        "rationale": "Clean higher-timeframe trend with volume confirmation.",
        "invalidation": "4h close back below 94",
    }
    base.update(overrides)
    return AnalystVerdict(**base)  # type: ignore[arg-type]


def _short(**overrides: object) -> AnalystVerdict:
    base: dict[str, object] = {
        "symbol": "SOLUSDT",
        "action": Action.SHORT,
        "confidence": 68.0,
        "entry": 100.0,
        "stop_loss": 106.0,
        "take_profits": (88.0, 80.0),
        "rationale": "Lower highs into resistance with BTC weak.",
        "invalidation": "4h close back above 106",
    }
    base.update(overrides)
    return AnalystVerdict(**base)  # type: ignore[arg-type]


def test_a_well_formed_long_is_accepted() -> None:
    assert validate_verdict(_long(), _brief()) is None


def test_a_well_formed_short_is_accepted() -> None:
    assert validate_verdict(_short(), _brief(direction=Direction.SHORT)) is None


def test_a_wait_verdict_needs_no_levels() -> None:
    verdict = AnalystVerdict(
        symbol="SOLUSDT",
        action=Action.WAIT,
        confidence=40.0,
        rationale="Wait for the 4h to close above the range high.",
    )

    assert validate_verdict(verdict, _brief()) is None


def test_a_long_stop_above_entry_is_rejected() -> None:
    reason = validate_verdict(_long(stop_loss=105.0), _brief())

    assert reason is not None
    assert "not below entry" in reason


def test_a_long_target_below_entry_is_rejected() -> None:
    reason = validate_verdict(_long(take_profits=(95.0,)), _brief())

    assert reason is not None
    assert "not above entry" in reason


def test_a_short_stop_below_entry_is_rejected() -> None:
    reason = validate_verdict(_short(stop_loss=95.0), _brief(direction=Direction.SHORT))

    assert reason is not None
    assert "not above entry" in reason


def test_a_short_target_above_entry_is_rejected() -> None:
    reason = validate_verdict(
        _short(take_profits=(115.0,)), _brief(direction=Direction.SHORT)
    )

    assert reason is not None
    assert "not below entry" in reason


@pytest.mark.parametrize("confidence", [-1.0, 100.1, 1_000.0])
def test_confidence_outside_the_range_is_rejected(confidence: float) -> None:
    reason = validate_verdict(_long(confidence=confidence), _brief())

    assert reason is not None
    assert "outside 0-100" in reason


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"entry": None}, "no positive entry"),
        ({"entry": 0.0}, "no positive entry"),
        ({"stop_loss": None}, "no positive stop-loss"),
        ({"take_profits": ()}, "no take-profit level"),
        ({"take_profits": (0.0,)}, "non-positive take-profit"),
        ({"invalidation": None}, "states no invalidation"),
        ({"invalidation": "   "}, "states no invalidation"),
    ],
)
def test_an_actionable_verdict_missing_a_required_field_is_rejected(
    override: dict[str, object], expected: str
) -> None:
    reason = validate_verdict(_long(**override), _brief())

    assert reason is not None
    assert expected in reason


@pytest.mark.parametrize("rationale", ["", "   "])
def test_an_empty_rationale_is_rejected(rationale: str) -> None:
    reason = validate_verdict(_long(rationale=rationale), _brief())

    assert reason == "rationale is empty"


def test_a_verdict_about_a_different_coin_is_rejected() -> None:
    reason = validate_verdict(_long(symbol="ETHUSDT"), _brief(symbol="SOLUSDT"))

    assert reason is not None
    assert "ETHUSDT" in reason and "SOLUSDT" in reason


def test_agreement_is_derived_from_the_engines_direction() -> None:
    assert agrees_with_engine(Action.LONG, Direction.LONG) is True
    assert agrees_with_engine(Action.LONG, Direction.SHORT) is False
    assert agrees_with_engine(Action.SHORT, Direction.SHORT) is True
    assert agrees_with_engine(Action.SHORT, Direction.LONG) is False


def test_standing_aside_is_not_counted_as_a_directional_disagreement() -> None:
    # WAIT/AVOID disagree about timing, not direction; marking them as conflicts would
    # make the disagreement column meaningless.
    assert agrees_with_engine(Action.WAIT, Direction.LONG) is True
    assert agrees_with_engine(Action.AVOID, Direction.SHORT) is True


def test_partition_splits_sound_verdicts_from_rejected_ones() -> None:
    briefs = [_brief("SOLUSDT"), _brief("ETHUSDT")]
    review = AnalystReview(
        verdicts=(_long(symbol="SOLUSDT"), _long(symbol="ETHUSDT", stop_loss=150.0))
    )

    accepted, rejected = partition_verdicts(review, briefs)

    assert [v.symbol for v in accepted] == ["SOLUSDT"]
    assert [r.verdict.symbol for r in rejected] == ["ETHUSDT"]
    assert "not below entry" in rejected[0].reason


def test_a_verdict_for_a_coin_that_was_never_sent_is_rejected() -> None:
    review = AnalystReview(verdicts=(_long(symbol="DOGEUSDT"),))

    accepted, rejected = partition_verdicts(review, [_brief("SOLUSDT")])

    assert accepted == ()
    assert "was not among the reviewed candidates" in rejected[0].reason


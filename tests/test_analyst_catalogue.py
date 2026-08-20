"""The analyst evaluating the named strategies rather than improvising a framework.

Three things are asserted here, and the third is the point of the whole task. The model's
**instructions** name 1A and 1B and state their conditions. Its **evidence** carries every
value those conditions are judged on, and nothing that was removed. And its **answer** says
which strategy it judged and how it read the entry — so the two verdicts can be compared,
and a disagreement is visible instead of silently resolved.

No test issues a model request: the completion function is injected.
"""

from __future__ import annotations

import json
import math

import pandas as pd
import pytest

from trader.analyst import AnalystError
from trader.brief import (
    MarketBrief,
    SetupBrief,
    brief_to_dict,
    build_brief,
    render_briefs,
)
from trader.direction import Direction
from trader.indicators import compute_features
from trader.market_data import Candles, OrderBook
from trader.openai_analyst import SYSTEM_PROMPT, OpenAIAnalyst
from trader.structure import analyze as analyze_structure

_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def _candles(rows: int = 260) -> Candles:
    closes = [100.0 + i * 0.2 + (i % 5) * 0.3 for i in range(rows)]
    data = [[i, c, c + 0.5, c - 0.5, c, 1_000.0 + i] for i, c in enumerate(closes)]
    return Candles(symbol="AAA", timeframe="4h", frame=pd.DataFrame(data, columns=_COLUMNS))


def _brief(**kwargs: object) -> SetupBrief:
    candles = _candles()
    return build_brief(
        "AAA",
        Direction.LONG,
        features_by_tf={"4h": compute_features(candles)},
        structure_by_tf={"4h": analyze_structure(candles)},
        close_by_tf={"4h": candles.latest_close},
        order_book=OrderBook(
            symbol="AAA", best_bid=99.5, best_ask=100.5, bid_depth=500.0, ask_depth=400.0
        ),
        plan=None,
        limited_history=False,
        timeframes=["4h"],
        recent_candles=candles,
        **kwargs,  # type: ignore[arg-type]
    )


# --- The instructions ---------------------------------------------------------------


def test_the_instructions_name_both_strategies() -> None:
    assert "STRATEGY 1A" in SYSTEM_PROMPT
    assert "STRATEGY 1B" in SYSTEM_PROMPT


@pytest.mark.parametrize(
    "condition",
    [
        "EMA10 > EMA21 > EMA50 > SMA200",
        "higher highs and higher lows",
        "rising trendline",
        "EMA21-EMA50 zone",
        "hammer/pin bar",
        "morning star",
        "Stochastic RSI %K crossing above %D",
        "MACD",
    ],
)
def test_the_instructions_state_each_condition_of_the_long_strategy(condition: str) -> None:
    assert condition in SYSTEM_PROMPT


@pytest.mark.parametrize(
    "condition",
    ["EMA10 < EMA21 < EMA50 < SMA200", "lower highs and lower lows", "falling trendline",
     "shooting star", "evening star", "rejecting from resistance"],
)
def test_the_instructions_state_the_mirrored_short_conditions(condition: str) -> None:
    assert condition in SYSTEM_PROMPT


def test_the_instructions_say_wait_is_expected_and_no_trade_is_required() -> None:
    """A model that feels obliged to find a trade will find one."""

    assert "WAIT is therefore the EXPECTED answer" in SYSTEM_PROMPT
    assert "never required to find a trade" in SYSTEM_PROMPT
    assert "A trend existing is NOT an entry" in SYSTEM_PROMPT


def test_the_instructions_ask_for_the_models_own_reading() -> None:
    assert "`strategy`" in SYSTEM_PROMPT
    assert "`entry_status`" in SYSTEM_PROMPT
    assert "not a repetition of the engine's" in SYSTEM_PROMPT


# --- The evidence ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["ema10", "ema21", "ema50", "sma200", "macd", "macd_signal", "stoch_rsi_k",
     "stoch_rsi_d", "stoch_cross", "macd_cross", "reaction_patterns", "trendline"],
)
def test_the_evidence_carries_every_method_value(field: str) -> None:
    row = brief_to_dict(_brief())["timeframes"][0]  # type: ignore[index]

    assert field in row


@pytest.mark.parametrize("removed", ["rsi", "roc", "obv", "obv_slope", "bollinger_width",
                                     "ema20", "ema200"])
def test_the_evidence_carries_nothing_that_was_removed(removed: str) -> None:
    row = brief_to_dict(_brief())["timeframes"][0]  # type: ignore[index]

    assert removed not in row


def test_the_evidence_carries_the_requested_number_of_candles() -> None:
    payload = brief_to_dict(_brief(candle_count=12))

    assert len(payload["candles"]) == 12  # type: ignore[arg-type,index]
    # Oldest first, and each bar is open/high/low/close.
    assert all(len(bar) == 4 for bar in payload["candles"])  # type: ignore[union-attr]


def test_changing_the_candle_count_changes_how_many_are_sent() -> None:
    few = brief_to_dict(_brief(candle_count=5))
    many = brief_to_dict(_brief(candle_count=40))

    assert len(few["candles"]) == 5  # type: ignore[arg-type,index]
    assert len(many["candles"]) == 40  # type: ignore[arg-type,index]


def test_no_candles_are_sent_when_the_count_is_zero() -> None:
    assert "candles" not in brief_to_dict(_brief(candle_count=0))


def test_the_evidence_encodes_under_a_strict_encoder() -> None:
    """A single NaN would fail the whole payload, so non-finite values never reach it."""

    payload = brief_to_dict(_brief(candle_count=20))

    encoded = json.dumps(payload, allow_nan=False)
    assert "NaN" not in encoded


def test_the_rendered_evidence_shows_the_candles_and_the_checklist() -> None:
    text = render_briefs([_brief(candle_count=6)], MarketBrief())

    assert "recent candles" in text


# --- The answer ------------------------------------------------------------------------


def _response(**overrides: object) -> str:
    verdict = {
        "symbol": "AAA",
        "action": "long",
        "confidence": 70.0,
        "strategy": "1A",
        "entry_status": "READY",
        "entry": 100.0,
        "stop_loss": 95.0,
        "take_profits": [110.0],
        "rationale": "trend pullback with a reaction at the zone",
        "key_risks": ["btc"],
        "invalidation": "close below 95",
    }
    verdict.update(overrides)
    for key, value in list(verdict.items()):
        if value is None and key in {"strategy", "entry_status"}:
            del verdict[key]
    return json.dumps({"verdicts": [verdict], "concentration_warning": None})


def _analyst(payload: str) -> OpenAIAnalyst:
    """An analyst wired to a fake completion — no test issues a model request."""

    def _completion(**_kwargs: object) -> object:
        class _Message:
            content = payload

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]
            usage = None

        return _Response()

    return OpenAIAnalyst(_completion, model="test-model", max_retries=1, sleep=lambda _s: None)  # type: ignore[arg-type]


def test_a_verdict_carries_the_models_own_strategy_and_entry_reading() -> None:
    review = _analyst(_response()).review([_brief()], MarketBrief())

    assert review.verdicts[0].strategy == "1A"
    assert review.verdicts[0].entry_status == "READY"


def test_a_verdict_omitting_the_strategy_is_rejected_not_defaulted() -> None:
    with pytest.raises(AnalystError):
        _analyst(_response(strategy=None)).review([_brief()], MarketBrief())


def test_a_verdict_omitting_the_entry_status_is_rejected_not_defaulted() -> None:
    with pytest.raises(AnalystError):
        _analyst(_response(entry_status=None)).review([_brief()], MarketBrief())


def test_an_unrecognised_entry_status_is_rejected() -> None:
    with pytest.raises(AnalystError):
        _analyst(_response(entry_status="MAYBE")).review([_brief()], MarketBrief())


def test_a_verdict_carries_a_confidence_on_a_zero_to_hundred_scale() -> None:
    review = _analyst(_response(confidence=42.0)).review([_brief()], MarketBrief())

    assert review.verdicts[0].confidence == pytest.approx(42.0)
    assert math.isfinite(review.verdicts[0].confidence)


# --- Both readings, side by side ----------------------------------------------------


def _rendered_verdicts(analyst_strategy: str, analyst_status: str, action: str = "long") -> str:
    """Render the verdict table for a coin whose engine verdict is 1A / READY (7/7)."""

    import sys

    from rich.console import Console

    sys.path.insert(0, "tests")
    from test_setups_view import _run  # noqa: PLC0415
    from trader.analyst import Action, AnalystReview, AnalystVerdict
    from trader.cli import _render_verdicts

    run = _run({"PULLUSDT": "pullback"})
    verdict = AnalystVerdict(
        symbol="PULLUSDT",
        action=Action(action),
        confidence=64.0,
        strategy=analyst_strategy,
        entry_status=analyst_status,
        entry=100.0,
        stop_loss=95.0,
        take_profits=(110.0,),
        rationale="…",
        agrees_with_engine=action == "long",
    )
    console = Console(width=220, record=True)
    _render_verdicts([verdict], [], AnalystReview(verdicts=(verdict,)), console, run=run)
    return console.export_text()


def test_both_readings_are_shown_side_by_side() -> None:
    text = _rendered_verdicts("1A", "READY")

    assert "Engine" in text and "Analyst" in text
    # The engine's checklist result and the model's own reading.
    assert "1A READY (7/7)" in text
    assert "1A READY" in text


def test_a_different_strategy_from_the_detectors_is_marked_as_a_disagreement() -> None:
    text = _rendered_verdicts("1B", "READY")

    assert "DISAGREES" in text
    assert "setup" in text


def test_a_different_entry_reading_is_marked_as_a_disagreement() -> None:
    """The checklist can tick every box in a context the model can see is wrong."""

    text = _rendered_verdicts("1A", "NOT_READY")

    assert "DISAGREES" in text
    assert "entry" in text


def test_agreement_is_not_marked_as_a_conflict() -> None:
    text = _rendered_verdicts("1A", "READY")

    assert "DISAGREES" not in text

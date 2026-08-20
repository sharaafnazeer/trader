"""Tests for the OpenAI-backed analyst.

Every test drives the class through an injected fake completion callable — no network, no
real client, no credential. The fake mirrors only the shape the adapter actually reads:
``choices[0].message.content`` plus ``usage``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from trader.analyst import Action, AnalystError
from trader.brief import LiquidityBrief, MarketBrief, SetupBrief
from trader.direction import Direction
from trader.openai_analyst import OpenAIAnalyst


class _Message:
    def __init__(self, content: str | None, refusal: str | None = None) -> None:
        self.content = content
        self.refusal = refusal


class _Choice:
    def __init__(self, message: _Message) -> None:
        self.message = message


class _Usage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _Response:
    def __init__(
        self,
        content: str | None,
        *,
        prompt_tokens: int = 1_200,
        completion_tokens: int = 300,
        refusal: str | None = None,
    ) -> None:
        self.choices = [_Choice(_Message(content, refusal))]
        self.usage = _Usage(prompt_tokens, completion_tokens)


class FakeCompletion:
    """Replays a scripted sequence of responses/exceptions and records every request."""

    def __init__(self, *outcomes: Any) -> None:
        self._outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        outcome = self._outcomes.pop(0) if self._outcomes else self._outcomes
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def call_count(self) -> int:
        return len(self.requests)


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


def _body(
    verdicts: list[dict[str, Any]] | None = None,
    concentration_warning: str | None = None,
) -> str:
    return json.dumps(
        {
            "verdicts": verdicts
            if verdicts is not None
            else [
                {
                    "symbol": "SOLUSDT",
                    "action": "long",
                    "confidence": 71.5,
                    "strategy": "1A",
                    "entry_status": "READY",
                    "entry": 100.0,
                    "stop_loss": 94.0,
                    "take_profits": [112.0, 120.0],
                    "rationale": "Trend intact, volume confirming.",
                    "key_risks": ["BTC rolling over"],
                    "invalidation": "4h close below 94",
                }
            ],
            "concentration_warning": concentration_warning,
        }
    )


def _analyst(complete: FakeCompletion, **overrides: Any) -> OpenAIAnalyst:
    kwargs: dict[str, Any] = {
        "model": "gpt-5",
        "max_retries": 2,
        "sleep": lambda _seconds: None,
    }
    kwargs.update(overrides)
    return OpenAIAnalyst(complete, **kwargs)


def test_a_well_formed_response_maps_to_a_review() -> None:
    complete = FakeCompletion(_Response(_body()))

    review = _analyst(complete).review([_brief()], MarketBrief())

    assert complete.call_count == 1
    assert len(review.verdicts) == 1
    verdict = review.verdicts[0]
    assert verdict.symbol == "SOLUSDT"
    assert verdict.action is Action.LONG
    assert verdict.confidence == pytest.approx(71.5)
    assert verdict.entry == pytest.approx(100.0)
    assert verdict.stop_loss == pytest.approx(94.0)
    assert verdict.take_profits == (112.0, 120.0)
    assert verdict.key_risks == ("BTC rolling over",)
    assert verdict.invalidation == "4h close below 94"
    assert review.model == "gpt-5"


def test_one_request_carries_every_candidate() -> None:
    complete = FakeCompletion(_Response(_body(verdicts=[])))
    briefs = [_brief("SOLUSDT"), _brief("ETHUSDT"), _brief("ADAUSDT")]

    _analyst(complete).review(briefs, MarketBrief())

    assert complete.call_count == 1
    prompt = complete.requests[0]["messages"][1]["content"]
    for symbol in ("SOLUSDT", "ETHUSDT", "ADAUSDT"):
        assert symbol in prompt


def test_no_candidates_makes_no_request() -> None:
    complete = FakeCompletion()

    review = _analyst(complete).review([], MarketBrief())

    assert complete.call_count == 0
    assert review.verdicts == ()


def test_the_request_states_the_advisory_only_constraint() -> None:
    complete = FakeCompletion(_Response(_body()))

    _analyst(complete).review([_brief()], MarketBrief())

    system = complete.requests[0]["messages"][0]["content"]
    assert "ADVISORY ONLY" in system
    assert "Never instruct anyone to place an order" in system
    assert "concentration_warning" in system


def test_the_request_asks_for_the_strict_json_schema() -> None:
    complete = FakeCompletion(_Response(_body()))

    _analyst(complete).review([_brief()], MarketBrief())

    response_format = complete.requests[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


def test_temperature_is_omitted_unless_configured() -> None:
    # Some reasoning models reject any explicit temperature, so an unset value must not
    # become a default that breaks them.
    without = FakeCompletion(_Response(_body()))
    _analyst(without).review([_brief()], MarketBrief())
    assert "temperature" not in without.requests[0]

    with_temp = FakeCompletion(_Response(_body()))
    _analyst(with_temp, temperature=0.2).review([_brief()], MarketBrief())
    assert with_temp.requests[0]["temperature"] == pytest.approx(0.2)


def test_agreement_with_the_engine_is_derived_not_taken_from_the_model() -> None:
    body = _body(
        verdicts=[
            {
                "symbol": "SOLUSDT",
                "action": "short",
                "confidence": 60.0,
                "strategy": "1A",
                "entry_status": "READY",
                "entry": 100.0,
                "stop_loss": 106.0,
                "take_profits": [88.0],
                "rationale": "Momentum is fading.",
                "key_risks": [],
                "invalidation": "close above 106",
            }
        ]
    )
    complete = FakeCompletion(_Response(body))

    # The engine said LONG; the model says SHORT.
    review = _analyst(complete).review([_brief(direction=Direction.LONG)], MarketBrief())

    assert review.verdicts[0].agrees_with_engine is False


def test_the_concentration_warning_is_carried_through() -> None:
    complete = FakeCompletion(_Response(_body(concentration_warning="4 of 5 are shorts")))

    review = _analyst(complete).review([_brief()], MarketBrief())

    assert review.concentration_warning == "4 of 5 are shorts"


def test_token_usage_is_read_from_the_response() -> None:
    complete = FakeCompletion(_Response(_body(), prompt_tokens=2_500, completion_tokens=400))

    review = _analyst(complete).review([_brief()], MarketBrief())

    assert review.prompt_tokens == 2_500
    assert review.completion_tokens == 400


def test_cost_is_estimated_from_the_configured_token_prices() -> None:
    complete = FakeCompletion(
        _Response(_body(), prompt_tokens=1_000_000, completion_tokens=500_000)
    )

    review = _analyst(
        complete, input_cost_per_mtok=1.25, output_cost_per_mtok=10.0
    ).review([_brief()], MarketBrief())

    assert review.estimated_cost_usd == pytest.approx(1.25 + 5.0)


def test_cost_is_unknown_rather_than_zero_when_no_prices_are_configured() -> None:
    complete = FakeCompletion(_Response(_body()))

    review = _analyst(complete).review([_brief()], MarketBrief())

    assert review.estimated_cost_usd is None


def test_a_malformed_response_is_retried_then_degrades_to_an_error() -> None:
    complete = FakeCompletion(
        _Response("not json at all"),
        _Response("still not json"),
        _Response("nope"),
    )

    with pytest.raises(AnalystError):
        _analyst(complete, max_retries=2).review([_brief()], MarketBrief())

    assert complete.call_count == 3


def test_a_response_missing_the_required_key_is_treated_as_malformed() -> None:
    complete = FakeCompletion(_Response(json.dumps({"concentration_warning": None})))

    with pytest.raises(AnalystError, match="verdicts"):
        _analyst(complete, max_retries=0).review([_brief()], MarketBrief())


def test_a_transport_error_is_retried_up_to_the_bound_then_degrades() -> None:
    complete = FakeCompletion(
        ConnectionError("boom"), ConnectionError("boom"), ConnectionError("boom")
    )

    with pytest.raises(AnalystError):
        _analyst(complete, max_retries=2).review([_brief()], MarketBrief())

    assert complete.call_count == 3


def test_a_retry_that_succeeds_returns_the_review() -> None:
    complete = FakeCompletion(ConnectionError("transient"), _Response(_body()))

    review = _analyst(complete, max_retries=2).review([_brief()], MarketBrief())

    assert complete.call_count == 2
    assert len(review.verdicts) == 1


def test_retries_back_off_between_attempts() -> None:
    delays: list[float] = []
    complete = FakeCompletion(ConnectionError("a"), ConnectionError("b"), _Response(_body()))

    _analyst(complete, max_retries=2, sleep=delays.append).review([_brief()], MarketBrief())

    assert delays == [1.0, 2.0]


def test_no_retries_configured_means_a_single_attempt() -> None:
    complete = FakeCompletion(ConnectionError("boom"))

    with pytest.raises(AnalystError):
        _analyst(complete, max_retries=0).review([_brief()], MarketBrief())

    assert complete.call_count == 1


def test_a_refusal_is_treated_as_a_failure_not_a_verdict() -> None:
    complete = FakeCompletion(_Response(None, refusal="I can't help with that"))

    with pytest.raises(AnalystError, match="refused"):
        _analyst(complete, max_retries=0).review([_brief()], MarketBrief())


def test_an_unknown_action_is_treated_as_malformed() -> None:
    body = _body(
        verdicts=[
            {
                "symbol": "SOLUSDT",
                "action": "moon",
                "confidence": 90.0,
                "strategy": "1A",
                "entry_status": "READY",
                "entry": 100.0,
                "stop_loss": 94.0,
                "take_profits": [112.0],
                "rationale": "up",
                "key_risks": [],
                "invalidation": "x",
            }
        ]
    )
    complete = FakeCompletion(_Response(body))

    with pytest.raises(AnalystError, match="moon"):
        _analyst(complete, max_retries=0).review([_brief()], MarketBrief())


def test_a_geometrically_impossible_verdict_is_returned_not_retried() -> None:
    # Semantic soundness is validation's job downstream; retrying would just burn tokens
    # re-asking a question the model already answered.
    body = _body(
        verdicts=[
            {
                "symbol": "SOLUSDT",
                "action": "long",
                "confidence": 80.0,
                "strategy": "1A",
                "entry_status": "READY",
                "entry": 100.0,
                "stop_loss": 150.0,
                "take_profits": [90.0],
                "rationale": "Impossible but well-formed.",
                "key_risks": [],
                "invalidation": "x",
            }
        ]
    )
    complete = FakeCompletion(_Response(body))

    review = _analyst(complete).review([_brief()], MarketBrief())

    assert complete.call_count == 1
    assert review.verdicts[0].stop_loss == pytest.approx(150.0)


def test_the_credential_never_appears_in_the_request_or_the_review() -> None:
    # The adapter is handed a completion callable, never a key: there is no path by which
    # a credential could reach the request payload or the returned review.
    complete = FakeCompletion(_Response(_body()))

    review = _analyst(complete).review([_brief()], MarketBrief())

    assert "api_key" not in complete.requests[0]
    assert "sk-" not in json.dumps(complete.requests[0], default=str)
    assert "sk-" not in repr(review)

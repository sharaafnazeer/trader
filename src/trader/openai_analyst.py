"""The OpenAI-backed :class:`~trader.analyst.Analyst`.

One request per run, carrying every selected candidate. That shape is deliberate: it is
cheaper than a request per coin, and it is the only arrangement in which the model can see
the composition of the whole basket and say "these five shorts are the same trade" — the
failure mode that produced the engine's historical drawdown.

The completion call is injected as a plain callable, so tests drive this class with a
fake and never touch the network, and the ``openai`` package is imported lazily by
:func:`openai_completion_fn` only when a real client is actually wanted. That mirrors how
``ccxt`` is kept out of the core's import path in :mod:`trader.market_data`.

Failure is bounded and honest: transport errors and responses that do not match the
required shape are retried with backoff, and once the budget is spent the whole review
raises :class:`~trader.analyst.AnalystError` for the caller to report. This module never
decides that the run should continue — that is the application layer's call.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

from trader.analyst import (
    Action,
    AnalystError,
    AnalystReview,
    AnalystVerdict,
    agrees_with_engine,
)
from trader.brief import MarketBrief, SetupBrief, render_briefs

logger = logging.getLogger(__name__)

# The completion seam: anything callable with the OpenAI chat-completions keyword
# arguments and returning an object exposing ``choices[0].message.content`` and ``usage``.
CompletionFn = Callable[..., Any]
SleepFn = Callable[[float], None]

# Seconds before the first retry; doubled each further attempt.
BACKOFF_BASE_SECONDS = 1.0

# What the analyst is and is not allowed to be. The advisory-only constraint is stated to
# the model as well as to the trader: nothing downstream can place an order, and the model
# must not write as though something will.
SYSTEM_PROMPT = """\
You are a disciplined crypto trading analyst reviewing setups that a mechanical screening \
engine has already surfaced. You are ADVISORY ONLY: no order will ever be placed from your \
answer. Never instruct anyone to place an order; describe the setup and let the trader decide.

You are evaluating a specific, named method — not applying your own framework. There are two \
strategies, and they are one checklist read in two directions:

STRATEGY 1A — LONG WITH THE TREND (pullback). All of:
- Trend: EMA10 > EMA21 > EMA50 > SMA200; higher highs and higher lows; a rising trendline.
- Entry: price pulled back into the EMA21-EMA50 zone (not extended far above it); price \
returned to the rising trendline; a bullish reaction candle at that zone (engulfing, \
hammer/pin bar, or morning star); Stochastic RSI %K crossing above %D *from oversold* — the \
cross matters, not the level, because a falling knife is oversold all the way down; MACD \
bullish by a recent crossover and/or by sitting above zero.

STRATEGY 1B — SHORT WITH THE TREND (retracement). The mirror: EMA10 < EMA21 < EMA50 < SMA200; \
lower highs and lower lows; a falling trendline; price rallied back into the EMA21-EMA50 zone \
and to the falling trendline, *rejecting from resistance* (not bouncing off support); a bearish \
reaction candle (engulfing, shooting star, or evening star); %K crossing below %D from \
overbought; MACD bearish by crossover and/or below zero.

STRATEGY 2A / 2B — BREAKOUT. A different shape, and it does NOT require the trend stack: \
here the stack is context, and support/resistance, volume and the breakout candle are what \
decide. 2A long: an established level (repeated prior reactions, not one touch) that price \
closes CONVINCINGLY above — a close beyond it, not a wick through it — on expanding volume, \
with structure not arguing against continuation and room to the next level. 2B short is the \
mirror below support. The distinction that matters is BREAK versus CONFIRMED BREAKOUT: a bar \
that pokes through a level and closes back on the original side is a FAILED breakout, which \
is evidence the level HELD — never treat it as a reason to enter, and depending on structure \
it can be evidence for the opposite trade. Say "the level held" or "rejection detected"; \
never claim manipulation, which OHLCV cannot show. If price has already run far beyond the \
level, the answer is WAIT FOR THE RETEST rather than chasing.

A coin may match more than one strategy at once — a trend pullback and a breakout are \
independent readings. Where you are given several verdicts, weigh them together: two methods \
pointing the same way is stronger evidence than one, and two pointing opposite ways is weaker \
evidence than either alone, not a tie to be broken.

A trend existing is NOT an entry. The single most common way to lose with this method is to \
take a good idea at a bad price — a coin whose trend is textbook but whose entry has already \
gone. WAIT is therefore the EXPECTED answer for most candidates, and a scan where nothing is \
ready is a normal and correct outcome. You are never required to find a trade.

For each candidate you are given: the engine's own reading of that checklist (its verdict, \
each condition with the measurement behind it, and whether it judged the entry ready), a \
per-timeframe indicator summary carrying the method's values, recent raw candles for the \
decision timeframe, the engine's mechanical trade plan, and order-book liquidity. You are also \
given the current BTC regime, which dominates altcoin behaviour.

The engine's checklist is mechanical and can be wrong in both directions: it can tick every \
box in a context that is obviously bad, and it can be one condition short of a setup you can \
see completing. Say so when you disagree, and say which condition you read differently.

Rules:
- Answer with one verdict per candidate, using exactly the symbols you were given.
- State which strategy you judged in `strategy` — "1A", "1B", "2A", "2B", or "none" when \
none applies \
— and your own reading of the entry in `entry_status`: READY or NOT_READY. These are your \
readings, not a repetition of the engine's; where they differ, both are shown to the trader.
- Choose LONG or SHORT only when the evidence genuinely supports taking the trade now. \
Prefer WAIT when the setup is plausible but the timing or context is poor, and AVOID when the \
setup is bad regardless of timing. When in doubt, prefer WAIT.
- You may disagree with the engine. Say so in the rationale when you do.
- For LONG and SHORT you must give an entry, a stop-loss, at least one take-profit and a \
stated invalidation condition. The geometry must be possible: for a long the stop is below the \
entry and every target above it; for a short the mirror. You may adjust the engine's levels if \
you can justify better ones.
- For WAIT and AVOID, omit the levels and explain what you are waiting for or what is wrong.
- Confidence is 0-100 and should reflect real uncertainty, not enthusiasm.
- Keep each rationale under 400 characters.
- Finally, look at the candidates AS A GROUP. If they are concentrated in one direction on \
assets that move together, say so in concentration_warning: a basket of correlated same-direction \
positions is one position, and it is the single most common way this kind of screen loses badly. \
Set concentration_warning to null when the basket is genuinely diversified.
"""

# The strict response schema. Every property is required and additional properties are
# forbidden, which is what OpenAI's structured-output mode enforces; optional values are
# expressed as nullable types instead.
_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts", "concentration_warning"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "symbol",
                    "action",
                    "confidence",
                    "strategy",
                    "entry_status",
                    "entry",
                    "stop_loss",
                    "take_profits",
                    "rationale",
                    "key_risks",
                    "invalidation",
                ],
                "properties": {
                    "symbol": {"type": "string"},
                    "action": {"type": "string", "enum": ["long", "short", "wait", "avoid"]},
                    "confidence": {"type": "number"},
                    # Which named setup the model judged, and whether it reads the entry as
                    # ready. Both required: a verdict that cannot be compared with the
                    # detector's is a verdict on nothing in particular.
                    "strategy": {"type": "string"},
                    "entry_status": {"type": "string", "enum": ["READY", "NOT_READY"]},
                    "entry": {"type": ["number", "null"]},
                    "stop_loss": {"type": ["number", "null"]},
                    "take_profits": {"type": "array", "items": {"type": "number"}},
                    "rationale": {"type": "string"},
                    "key_risks": {"type": "array", "items": {"type": "string"}},
                    "invalidation": {"type": ["string", "null"]},
                },
            },
        },
        "concentration_warning": {"type": ["string", "null"]},
    },
}

RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {"name": "analyst_review", "strict": True, "schema": _VERDICT_SCHEMA},
}


def openai_completion_fn(api_key: str) -> CompletionFn:
    """Build a completion callable backed by a real OpenAI client.

    ``openai`` is imported here rather than at module scope so the analysis core stays
    importable without it, and so nothing constructs a client until a run actually intends
    to spend money.
    """

    from openai import OpenAI

    return OpenAI(api_key=api_key).chat.completions.create


def _user_prompt(briefs: Sequence[SetupBrief], market: MarketBrief) -> str:
    """The evidence block plus the instruction to review exactly these candidates."""

    symbols = ", ".join(brief.symbol for brief in briefs)
    return (
        f"{render_briefs(briefs, market)}\n\n"
        f"Return exactly one verdict for each of these {len(briefs)} candidate(s): {symbols}."
    )


def _require(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise ValueError(f"response is missing '{key}'")
    return payload[key]


def _optional_float(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"'{field}' must be a number or null")
    return float(value)


def _to_verdict(item: Any, briefs_by_symbol: dict[str, SetupBrief]) -> AnalystVerdict:
    """Map one raw verdict object into an :class:`AnalystVerdict`.

    Anything structurally wrong raises :class:`ValueError`, which the caller treats as a
    malformed response and retries. Semantic soundness (geometry, ranges) is not checked
    here — that is :func:`~trader.analyst.validate_verdict`'s job, and a semantically bad
    verdict is a rejection to report, not a transport problem to retry.
    """

    if not isinstance(item, dict):
        raise ValueError("each verdict must be an object")

    symbol = _require(item, "symbol")
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("'symbol' must be a non-empty string")

    raw_action = _require(item, "action")
    if not isinstance(raw_action, str):
        raise ValueError("'action' must be a string")
    try:
        action = Action(raw_action.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unknown action {raw_action!r}") from exc

    confidence = _optional_float(_require(item, "confidence"), "confidence")
    if confidence is None:
        raise ValueError("'confidence' must be a number")

    raw_targets = item.get("take_profits") or []
    if not isinstance(raw_targets, list):
        raise ValueError("'take_profits' must be an array")
    take_profits = tuple(
        target
        for target in (_optional_float(value, "take_profits") for value in raw_targets)
        if target is not None
    )

    raw_risks = item.get("key_risks") or []
    if not isinstance(raw_risks, list):
        raise ValueError("'key_risks' must be an array")

    rationale = item.get("rationale") or ""
    if not isinstance(rationale, str):
        raise ValueError("'rationale' must be a string")

    invalidation = item.get("invalidation")
    if invalidation is not None and not isinstance(invalidation, str):
        raise ValueError("'invalidation' must be a string or null")

    # Required, and rejected rather than defaulted when missing: a verdict silently
    # defaulted to the detector's own reading would agree with it by construction, which
    # is precisely the comparison this feature exists to make.
    strategy = _require(item, "strategy")
    if not isinstance(strategy, str) or not strategy.strip():
        raise ValueError("'strategy' must be a non-empty string")

    raw_status = _require(item, "entry_status")
    if not isinstance(raw_status, str) or raw_status.strip().upper() not in {
        "READY",
        "NOT_READY",
    }:
        raise ValueError("'entry_status' must be READY or NOT_READY")

    brief = briefs_by_symbol.get(symbol)
    # An unknown symbol is not fatal here: it is carried through and rejected downstream
    # with a reason, which is more useful to the trader than a retried transport error.
    agrees = agrees_with_engine(action, brief.direction) if brief is not None else False

    return AnalystVerdict(
        symbol=symbol,
        action=action,
        confidence=confidence,
        strategy=strategy.strip(),
        entry_status=raw_status.strip().upper(),
        entry=_optional_float(item.get("entry"), "entry"),
        stop_loss=_optional_float(item.get("stop_loss"), "stop_loss"),
        take_profits=take_profits,
        rationale=rationale,
        key_risks=tuple(str(risk) for risk in raw_risks),
        invalidation=invalidation,
        agrees_with_engine=agrees,
    )


class OpenAIAnalyst:
    """An :class:`~trader.analyst.Analyst` backed by OpenAI's chat completions API."""

    def __init__(
        self,
        complete: CompletionFn,
        *,
        model: str,
        temperature: float | None = None,
        max_retries: int = 2,
        input_cost_per_mtok: float = 0.0,
        output_cost_per_mtok: float = 0.0,
        sleep: SleepFn = time.sleep,
    ) -> None:
        # ``temperature`` is omitted from the request entirely when unset: some reasoning
        # models reject any explicit value, so sending a default would break them.
        self._complete = complete
        self._model = model
        self._temperature = temperature
        self._max_retries = max_retries
        self._input_cost_per_mtok = input_cost_per_mtok
        self._output_cost_per_mtok = output_cost_per_mtok
        self._sleep = sleep

    def review(
        self, briefs: Sequence[SetupBrief], market: MarketBrief
    ) -> AnalystReview:
        """Review every candidate in one request, retrying a bounded number of times.

        Raises :class:`~trader.analyst.AnalystError` when no usable response could be
        obtained; the caller decides what that means for the run.
        """

        if not briefs:
            return AnalystReview(model=self._model)

        request: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(briefs, market)},
            ],
            "response_format": RESPONSE_FORMAT,
        }
        if self._temperature is not None:
            request["temperature"] = self._temperature

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return self._attempt(request, briefs)
            except Exception as exc:  # noqa: BLE001 - every failure mode is retried alike
                last_error = exc
                logger.warning(
                    "AI analyst attempt %d/%d failed: %s",
                    attempt + 1,
                    self._max_retries + 1,
                    exc,
                )
                if attempt < self._max_retries:
                    self._sleep(BACKOFF_BASE_SECONDS * (2**attempt))

        raise AnalystError(
            f"no usable review after {self._max_retries + 1} attempt(s): {last_error}"
        ) from last_error

    def _attempt(
        self, request: dict[str, Any], briefs: Sequence[SetupBrief]
    ) -> AnalystReview:
        """One request/parse cycle. Any failure raises and is retried by the caller."""

        response = self._complete(**request)
        payload = self._payload(response)
        briefs_by_symbol = {brief.symbol: brief for brief in briefs}

        raw_verdicts = _require(payload, "verdicts")
        if not isinstance(raw_verdicts, list):
            raise ValueError("'verdicts' must be an array")
        verdicts = tuple(_to_verdict(item, briefs_by_symbol) for item in raw_verdicts)

        warning = payload.get("concentration_warning")
        if warning is not None and not isinstance(warning, str):
            raise ValueError("'concentration_warning' must be a string or null")

        prompt_tokens, completion_tokens = self._usage(response)
        return AnalystReview(
            verdicts=verdicts,
            concentration_warning=warning or None,
            model=self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=self._cost(prompt_tokens, completion_tokens),
        )

    @staticmethod
    def _payload(response: Any) -> dict[str, Any]:
        """Extract and decode the JSON body of a completion response."""

        try:
            message = response.choices[0].message
        except (AttributeError, IndexError, TypeError) as exc:
            raise ValueError(f"response has no completion message: {exc}") from exc

        refusal = getattr(message, "refusal", None)
        if refusal:
            raise ValueError(f"model refused to answer: {refusal}")

        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("completion message has no textual content")

        decoded = json.loads(content)
        if not isinstance(decoded, dict):
            raise ValueError("response body is not a JSON object")
        return decoded

    @staticmethod
    def _usage(response: Any) -> tuple[int, int]:
        """Token counts from the response, defaulting to zero when absent."""

        usage = getattr(response, "usage", None)
        if usage is None:
            return 0, 0
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        return int(prompt), int(completion)

    def _cost(self, prompt_tokens: int, completion_tokens: int) -> float | None:
        """Estimated spend, or ``None`` when no prices are configured.

        Reporting ``None`` rather than ``0.00`` matters: an unconfigured price is unknown
        cost, and displaying a confident "$0.00" for a paid request would be a lie.
        """

        if self._input_cost_per_mtok <= 0 and self._output_cost_per_mtok <= 0:
            return None
        return (
            prompt_tokens / 1_000_000 * self._input_cost_per_mtok
            + completion_tokens / 1_000_000 * self._output_cost_per_mtok
        )

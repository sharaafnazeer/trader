"""The AI analyst's vocabulary: what an opinion is, and what makes one usable.

The engine decides *whether* a setup is technically valid; the analyst decides whether it
is worth taking right now. Its answer is an :class:`AnalystVerdict` per candidate —
:class:`Action`, confidence, concrete levels, the invalidation it would respect, its
reasoning and the risks it sees — wrapped in an :class:`AnalystReview` that also carries
the model's read on the *whole basket* and what the request cost.

:func:`validate_verdict` is the safety gate. A language model can return a confident,
well-written, geometrically impossible trade, and showing that to a trader is worse than
showing nothing. Anything that fails validation is rejected and reported as a failure
rather than displayed as something to act on.

``agrees_with_engine`` is computed here from the engine's own decided direction, never
taken from the model — whether the two disagree is a fact about our data, not an opinion.

This module belongs to the analysis core: it is pure and deterministic, performs no I/O,
and does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from trader.brief import MarketBrief, SetupBrief
from trader.direction import Direction


class Action(StrEnum):
    """What the analyst says to do about a candidate."""

    LONG = "long"
    SHORT = "short"
    WAIT = "wait"
    AVOID = "avoid"


# The two actions that describe a trade. Only these carry levels, and only these can be
# alerted on: a high-confidence WAIT is still a WAIT.
ACTIONABLE: frozenset[Action] = frozenset({Action.LONG, Action.SHORT})


class AnalystError(Exception):
    """Raised when a review could not be obtained at all (transport, shape, refusal)."""


@dataclass(frozen=True)
class AnalystVerdict:
    """The analyst's opinion on one candidate.

    ``entry``/``stop_loss``/``take_profits`` are the analyst's own levels, which may
    differ from the engine's mechanical plan — when they do, the analyst's are the ones
    shown, because a better-reasoned entry is the point of asking. ``invalidation`` is the
    condition it would treat as proof the setup is wrong, in words rather than a price.
    ``agrees_with_engine`` is derived, not reported.

    ``strategy`` and ``entry_status`` are the model's own reading of *which* setup it judged
    and whether it considers the entry ready — what makes the two verdicts comparable, since
    without them a disagreement is invisible. They carry conservative defaults here so the
    class stays constructible, but a *model response* omitting either is rejected outright
    rather than defaulted: a verdict silently filled in from the engine's own reading would
    agree with it by construction, which is exactly the comparison being made.
    """

    symbol: str
    action: Action
    confidence: float
    entry: float | None = None
    stop_loss: float | None = None
    take_profits: tuple[float, ...] = ()
    rationale: str = ""
    key_risks: tuple[str, ...] = ()
    invalidation: str | None = None
    agrees_with_engine: bool = True
    strategy: str = "none"
    entry_status: str = "NOT_READY"

    @property
    def is_actionable(self) -> bool:
        """Whether this verdict describes a trade rather than a decision to stand aside."""
        return self.action in ACTIONABLE


@dataclass(frozen=True)
class AnalystReview:
    """One request's worth of opinions, plus what it observed and what it cost.

    ``concentration_warning`` is the model's read on the basket as a whole — the reason
    every candidate is reviewed in a single request. ``estimated_cost_usd`` is ``None``
    when no token prices are configured, which is honestly "unknown" rather than "free".
    """

    verdicts: tuple[AnalystVerdict, ...] = ()
    concentration_warning: str | None = None
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float | None = None


@dataclass(frozen=True)
class RejectedVerdict:
    """A verdict that failed validation, with the reason it was not shown."""

    verdict: AnalystVerdict
    reason: str


class Analyst(Protocol):
    """Reviews a run's selected candidates and returns one verdict per candidate."""

    def review(
        self, briefs: Sequence[SetupBrief], market: MarketBrief
    ) -> AnalystReview: ...


def agrees_with_engine(action: Action, direction: Direction) -> bool:
    """Whether an action lines up with the direction the engine decided.

    Standing aside (``WAIT``/``AVOID``) is not a disagreement about direction — it is a
    disagreement about timing — so it never marks the row as a conflict. Only an opposite
    trade does.
    """

    if action is Action.LONG:
        return direction is Direction.LONG
    if action is Action.SHORT:
        return direction is Direction.SHORT
    return True


def validate_verdict(verdict: AnalystVerdict, brief: SetupBrief) -> str | None:
    """Return why this verdict must not be shown as tradeable, or ``None`` if it is sound.

    Checks, in order: the verdict is about the coin it claims to be about; confidence is a
    real percentage; the rationale says something. An actionable verdict must additionally
    carry positive levels, state an invalidation, and be geometrically possible — for a
    long the stop sits below the entry and every target above it, and for a short the
    mirror. A verdict that fails any of these is a failure to record, not a plan to trade.
    """

    if verdict.symbol != brief.symbol:
        return f"verdict is for {verdict.symbol}, expected {brief.symbol}"

    if not 0.0 <= verdict.confidence <= 100.0:
        return f"confidence {verdict.confidence:g} is outside 0-100"

    if not verdict.rationale.strip():
        return "rationale is empty"

    if not verdict.is_actionable:
        return None

    if verdict.entry is None or verdict.entry <= 0:
        return "actionable verdict has no positive entry"
    if verdict.stop_loss is None or verdict.stop_loss <= 0:
        return "actionable verdict has no positive stop-loss"
    if not verdict.take_profits:
        return "actionable verdict has no take-profit level"
    if any(target <= 0 for target in verdict.take_profits):
        return "actionable verdict has a non-positive take-profit level"
    if verdict.invalidation is None or not verdict.invalidation.strip():
        return "actionable verdict states no invalidation"

    entry = verdict.entry
    stop = verdict.stop_loss
    if verdict.action is Action.LONG:
        if stop >= entry:
            return f"long stop-loss {stop:g} is not below entry {entry:g}"
        if min(verdict.take_profits) <= entry:
            return f"long take-profit {min(verdict.take_profits):g} is not above entry {entry:g}"
    else:
        if stop <= entry:
            return f"short stop-loss {stop:g} is not above entry {entry:g}"
        if max(verdict.take_profits) >= entry:
            return (
                f"short take-profit {max(verdict.take_profits):g} is not below entry {entry:g}"
            )

    return None


def partition_verdicts(
    review: AnalystReview, briefs: Sequence[SetupBrief]
) -> tuple[tuple[AnalystVerdict, ...], tuple[RejectedVerdict, ...]]:
    """Split a review's verdicts into the ones safe to show and the ones to reject.

    A verdict naming a coin that was never sent is rejected outright: it is either a
    hallucination or a mismatch, and neither is something to display.
    """

    briefs_by_symbol = {brief.symbol: brief for brief in briefs}
    accepted: list[AnalystVerdict] = []
    rejected: list[RejectedVerdict] = []

    for verdict in review.verdicts:
        brief = briefs_by_symbol.get(verdict.symbol)
        if brief is None:
            rejected.append(
                RejectedVerdict(verdict, f"{verdict.symbol} was not among the reviewed candidates")
            )
            continue
        reason = validate_verdict(verdict, brief)
        if reason is None:
            accepted.append(verdict)
        else:
            rejected.append(RejectedVerdict(verdict, reason))

    return tuple(accepted), tuple(rejected)

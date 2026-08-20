"""The append-only record of every opinion the AI analyst formed.

This is what makes the analyst falsifiable. The engine's own measured edge is thin — a
profit factor of 1.10 over 1,362 backtested trades — so an opinion layer bolted on top is
indistinguishable from an expensive random filter until someone can compare "engine alone"
against "engine plus analyst" on the same setups. That comparison needs both halves stored
together, at the moment of the decision, which is exactly what a record is: the engine's
score and mechanical plan beside the analyst's verdict.

Every selected candidate produces exactly one record, including the ones whose verdict was
rejected as unusable and the ones whose review never came back. A silent gap in the history
would quietly bias any later measurement toward the cases that happened to work.

Records are line-delimited JSON — one self-contained object per line, appended, never
rewritten — so the file can be tailed, grepped, or read by a later measurement tool with
no schema migration and no database. ``schema_version`` is carried on every line so that
tool can evolve.

This module belongs to the analysis core: it does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from trader.analyst import AnalystReview, AnalystVerdict, RejectedVerdict
from trader.brief import SetupBrief

logger = logging.getLogger(__name__)

# Bumped whenever the record shape changes in a way a reader must know about.
SCHEMA_VERSION = 1

# Which scanner produced the candidate. Recorded so the two engines' contributions can be
# measured separately from a single file.
SCANNER_SCAN = "scan"
SCANNER_MOVERS = "movers"

# What became of the candidate's review.
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"

ClockFn = Callable[[], float]


@dataclass(frozen=True)
class DecisionRecord:
    """One candidate's review outcome, paired with what the engine thought of it.

    ``status`` is ``accepted`` when the verdict passed validation, ``rejected`` when it
    was unusable (with ``reason``), and ``failed`` when no verdict was obtained at all
    (also with ``reason``). The analyst fields are ``None`` on a failed record — there was
    nothing to record — while the engine fields are always present, because the engine's
    opinion is what the analyst is being measured against.
    """

    scanner: str
    symbol: str
    status: str
    engine_direction: str
    engine_total: float | None
    engine_plan: dict[str, float] | None = None
    limited_history: bool = False
    action: str | None = None
    confidence: float | None = None
    entry: float | None = None
    stop_loss: float | None = None
    take_profits: tuple[float, ...] = ()
    invalidation: str | None = None
    rationale: str | None = None
    key_risks: tuple[str, ...] = ()
    agrees_with_engine: bool | None = None
    concentration_warning: str | None = None
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float | None = None
    reason: str | None = None


def _engine_plan(brief: SetupBrief) -> dict[str, float] | None:
    """The engine's mechanical plan for this setup, as plain numbers."""

    if brief.plan is None:
        return None
    return {
        "entry": brief.plan.entry,
        "stop_loss": brief.plan.stop_loss,
        "take_profit": brief.plan.take_profit,
        "risk_reward": brief.plan.risk_reward,
        "invalidation": brief.plan.invalidation,
    }


def _record(
    *,
    scanner: str,
    status: str,
    symbol: str,
    brief: SetupBrief | None = None,
    verdict: AnalystVerdict | None = None,
    review: AnalystReview | None = None,
    model: str = "",
    reason: str | None = None,
) -> DecisionRecord:
    """Assemble a record from whichever halves exist.

    One constructor for all three outcomes, so a field can never be populated on an
    accepted record and silently forgotten on a rejected one. ``brief`` is absent only for
    a verdict naming a coin that was never sent.
    """

    return DecisionRecord(
        scanner=scanner,
        symbol=symbol,
        status=status,
        engine_direction=brief.direction.value if brief is not None else "",
        engine_total=brief.total if brief is not None else None,
        engine_plan=_engine_plan(brief) if brief is not None else None,
        limited_history=brief.limited_history if brief is not None else False,
        action=verdict.action.value if verdict is not None else None,
        confidence=verdict.confidence if verdict is not None else None,
        entry=verdict.entry if verdict is not None else None,
        stop_loss=verdict.stop_loss if verdict is not None else None,
        take_profits=verdict.take_profits if verdict is not None else (),
        invalidation=verdict.invalidation if verdict is not None else None,
        rationale=verdict.rationale if verdict is not None else None,
        key_risks=verdict.key_risks if verdict is not None else (),
        agrees_with_engine=verdict.agrees_with_engine if verdict is not None else None,
        concentration_warning=review.concentration_warning if review is not None else None,
        model=review.model if review is not None else model,
        prompt_tokens=review.prompt_tokens if review is not None else 0,
        completion_tokens=review.completion_tokens if review is not None else 0,
        estimated_cost_usd=review.estimated_cost_usd if review is not None else None,
        reason=reason,
    )


def records_for_review(
    scanner: str,
    briefs: Sequence[SetupBrief],
    review: AnalystReview,
    accepted: Sequence[AnalystVerdict],
    rejected: Sequence[RejectedVerdict],
) -> tuple[DecisionRecord, ...]:
    """Build one record per reviewed candidate, in the order they were selected.

    A candidate the model simply did not answer for gets a ``failed`` record rather than
    being omitted, so the file always accounts for everything that was paid for.
    """

    briefs_by_symbol = {brief.symbol: brief for brief in briefs}
    accepted_by_symbol = {verdict.symbol: verdict for verdict in accepted}
    rejected_by_symbol = {item.verdict.symbol: item for item in rejected}

    records: list[DecisionRecord] = []
    for brief in briefs:
        verdict = accepted_by_symbol.get(brief.symbol)
        if verdict is not None:
            records.append(
                _record(
                    scanner=scanner,
                    status=STATUS_ACCEPTED,
                    symbol=brief.symbol,
                    brief=brief,
                    verdict=verdict,
                    review=review,
                )
            )
            continue

        item = rejected_by_symbol.get(brief.symbol)
        if item is not None:
            records.append(
                _record(
                    scanner=scanner,
                    status=STATUS_REJECTED,
                    symbol=brief.symbol,
                    brief=brief,
                    verdict=item.verdict,
                    review=review,
                    reason=item.reason,
                )
            )
            continue

        records.append(
            _record(
                scanner=scanner,
                status=STATUS_FAILED,
                symbol=brief.symbol,
                brief=brief,
                review=review,
                reason="the review returned no verdict for this candidate",
            )
        )

    # Verdicts naming a coin that was never sent are rejected upstream and have no brief to
    # pair with; they are still recorded so a hallucinating model leaves a trace.
    for item in rejected:
        if item.verdict.symbol not in briefs_by_symbol:
            records.append(
                _record(
                    scanner=scanner,
                    status=STATUS_REJECTED,
                    symbol=item.verdict.symbol,
                    verdict=item.verdict,
                    review=review,
                    reason=item.reason,
                )
            )

    return tuple(records)


def records_for_failure(
    scanner: str, briefs: Sequence[SetupBrief], reason: str, model: str = ""
) -> tuple[DecisionRecord, ...]:
    """Build one ``failed`` record per selected candidate when no review came back."""

    return tuple(
        _record(
            scanner=scanner,
            status=STATUS_FAILED,
            symbol=brief.symbol,
            brief=brief,
            model=model,
            reason=reason,
        )
        for brief in briefs
    )


class DecisionLog:
    """Appends decision records to a line-delimited JSON file.

    The clock is injected so tests can assert the stamped time, and the file is opened in
    append mode per write so a long-running watch loop never holds a handle open across
    hours and never loses buffered records to an interrupt.
    """

    def __init__(self, path: str, clock: ClockFn = time.time) -> None:
        self._path = Path(path)
        self._clock = clock

    @property
    def path(self) -> Path:
        """Where records are being written."""
        return self._path

    def append(self, record: DecisionRecord) -> None:
        """Append one record, stamped with the current time."""
        self.append_all([record])

    def append_all(self, records: Sequence[DecisionRecord]) -> None:
        """Append several records in order, each stamped with the current time."""

        if not records:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(self.to_dict(record)) + "\n")
        logger.info("wrote %d decision record(s) to %s", len(records), self._path)

    def to_dict(self, record: DecisionRecord) -> dict[str, object]:
        """Serialize a record, stamping it with the injected clock's current time."""

        stamped = datetime.fromtimestamp(self._clock(), tz=UTC).isoformat()
        return {
            "schema_version": SCHEMA_VERSION,
            "timestamp": stamped,
            "scanner": record.scanner,
            "symbol": record.symbol,
            "status": record.status,
            "engine_direction": record.engine_direction,
            "engine_total": record.engine_total,
            "engine_plan": record.engine_plan,
            "limited_history": record.limited_history,
            "action": record.action,
            "confidence": record.confidence,
            "entry": record.entry,
            "stop_loss": record.stop_loss,
            "take_profits": list(record.take_profits),
            "invalidation": record.invalidation,
            "rationale": record.rationale,
            "key_risks": list(record.key_risks),
            "agrees_with_engine": record.agrees_with_engine,
            "concentration_warning": record.concentration_warning,
            "model": record.model,
            "prompt_tokens": record.prompt_tokens,
            "completion_tokens": record.completion_tokens,
            "estimated_cost_usd": record.estimated_cost_usd,
            "reason": record.reason,
        }

"""Which surfaced setups are worth an AI-analyst review.

The local engine is the cheap first filter; this module is the second. It decides how
much of a scan is ever handed to a language model, and it is the reason a continuously
polling scanner over an 87-coin watchlist does not translate into an unbounded bill.

Three controls are applied here:

* a **score floor** — a setup the engine itself is lukewarm about is not worth an opinion;
* a **per-candle cooldown** — a coin already reviewed in this direction on this reference
  candle is not reviewed again until the market actually produces a new one;
* a **per-run cap** — at most N candidates, highest engine total first.

The cooldown is what makes a long-running poll affordable. ``scan --watch 300`` over a 4h
reference timeframe polls 48 times per candle; without the cooldown every one of those
polls would re-select — and re-pay for — an opinion on a setup that has not changed.

The pool the score floor is applied to is the whole **scored watchlist**, not the trade
list: a coin reaches the analyst when it resolved a direction and produced a usable plan.
``ai.min_score`` remains as the momentum scanner's floor, which is the only scanner still
producing a 0-100 figure to floor. See :func:`pair_candidates`.

Everything the evidence needs — per-timeframe features,
structure, trade plan, order book, limited-history marker — lives on the run's per-coin
:class:`~trader.runner.CoinAnalysis` records. :func:`pair_candidates` performs that join
once, so downstream code never has to re-index the run.

This module belongs to the analysis core: it is pure and deterministic, performs no I/O,
and does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from trader.direction import Direction
from trader.momentum import MomentumScore
from trader.movers import MomentumCoin, MoversRun
from trader.runner import AnalysisRun, CoinAnalysis

# A review is identified by the coin, the direction reviewed, and the reference candle it
# was reviewed on. Any of the three changing means the situation is genuinely new.
CooldownKey = tuple[str, Direction, int]


class Selectable(Protocol):
    """What :func:`select` needs from a candidate, whichever scanner produced it.

    Both engines decide a direction and can name the candle their view was formed on —
    which is all the gate reasons about. Keeping it to a protocol means the cap and the
    cooldown are one implementation serving both scanners rather than two that can drift
    apart.

    ``total`` is the candidate's 0-100 figure where one exists. The momentum scanner has
    one; the trend engine no longer does, since its quality score was retired with the
    indicators behind it, so it reports ``None`` and the score floor simply does not apply
    to it. A conditions-met floor replaces it for the trend path in a later task.
    """

    @property
    def symbol(self) -> str: ...

    @property
    def total(self) -> float | None: ...

    @property
    def direction(self) -> Direction: ...

    def cooldown_key(self, reference_timeframe: str) -> CooldownKey | None: ...


S = TypeVar("S", bound=Selectable)


@dataclass(frozen=True, eq=False)
class Candidate:
    """A surfaced setup, which since the score was retired is just its per-coin analysis.

    Kept as a wrapper rather than collapsed into :class:`~trader.runner.CoinAnalysis` so
    that the selection protocol has one shape for both scanners, and so the strategy
    verdict has somewhere to live when it arrives.

    ``eq=False`` because the wrapped analysis holds candle frames that are not
    scalar-comparable; identity is sufficient for how candidates are used.
    """

    analysis: CoinAnalysis

    @property
    def symbol(self) -> str:
        """The candidate's symbol."""
        return self.analysis.symbol

    @property
    def total(self) -> float | None:
        """No 0-100 total: the trend engine's quality score was retired."""
        return None

    @property
    def direction(self) -> Direction:
        """The direction the engine decided for this setup."""
        return self.analysis.direction

    def cooldown_key(self, reference_timeframe: str) -> CooldownKey | None:
        """This candidate's cooldown identity, or ``None`` when it cannot be keyed.

        The key is read from the reference timeframe's candles on the coin's analysis —
        the same timeframe the trade plan's levels are drawn from. A run that did not
        fetch that timeframe cannot be keyed; the caller then treats the candidate as
        always eligible, because silently suppressing a review is worse than paying for
        an occasional duplicate.
        """

        candles = self.analysis.candles_by_tf.get(reference_timeframe)
        if candles is None:
            return None
        return (self.symbol, self.direction, candles.latest_open_time)


@dataclass(frozen=True, eq=False)
class MoverCandidate:
    """A surfaced momentum mover joined to the per-coin record behind it.

    The momentum path's ranked entries carry only the score and its factors; the trade
    plan, order book and candles the evidence needs live on the per-coin record, so the
    two are paired here exactly as the trend path pairs its own halves.
    """

    score: MomentumScore
    coin: MomentumCoin

    @property
    def symbol(self) -> str:
        """The candidate's symbol."""
        return self.score.symbol

    @property
    def total(self) -> float:
        """The 0-100 composite momentum score."""
        return self.score.score

    @property
    def direction(self) -> Direction:
        """The direction implied by the sign of the coin's momentum."""
        return self.score.direction

    def cooldown_key(self, reference_timeframe: str) -> CooldownKey | None:
        """This mover's cooldown identity, keyed on its own (daily) candle.

        The momentum scanner fetches exactly one timeframe, and that is the one it keys
        on, so ``reference_timeframe`` — which describes the *trend* engine's level
        timeframe — is deliberately ignored here. In practice this means at most one
        review per coin and direction per day.
        """

        return (self.symbol, self.direction, self.coin.candles.latest_open_time)


def pair_movers(run: MoversRun) -> tuple[MoverCandidate, ...]:
    """Join a momentum run's surfaced movers to their per-coin records, in rank order.

    Only surfaced movers are eligible: a coin filtered out for illiquidity or for falling
    below the momentum threshold was never a candidate, and paying for an opinion on it
    would defeat the point of having a local filter at all.
    """

    coins_by_symbol = {coin.symbol: coin for coin in run.coins if coin.surfaced}
    return tuple(
        MoverCandidate(score=score, coin=coins_by_symbol[score.symbol])
        for score in run.movers
        if score.symbol in coins_by_symbol
    )


class CooldownState:
    """Remembers which coin/direction was last reviewed, and on which candle.

    Owned by the caller — the watch loop holds one instance for its whole life and passes
    it into every iteration — so :func:`select` can stay pure. Only one entry is kept per
    coin and direction: the cooldown asks "has this already been reviewed on the current
    candle?", so older candles carry no information and are overwritten.
    """

    def __init__(self) -> None:
        self._reviewed: dict[tuple[str, Direction], int] = {}

    def is_cooling(self, key: CooldownKey) -> bool:
        """Whether this exact coin/direction/candle combination was already reviewed."""
        symbol, direction, bar_open_ms = key
        return self._reviewed.get((symbol, direction)) == bar_open_ms

    def record(self, key: CooldownKey) -> None:
        """Remember that this coin/direction was reviewed on this candle."""
        symbol, direction, bar_open_ms = key
        self._reviewed[(symbol, direction)] = bar_open_ms

    def snapshot(self) -> dict[tuple[str, Direction], int]:
        """A copy of the recorded reviews, for assertions and diagnostics."""
        return dict(self._reviewed)


def _rank_key(candidate: Selectable) -> tuple[float, str]:
    """Sort key: best first, then symbol for a deterministic tie-break.

    "Best" means different things to the two scanners and neither is a stand-in for the
    other. The momentum scanner has a 0-100 composite. The trend engine has no score — it
    was retired with the indicators behind it — so it ranks by how many checklist conditions
    are satisfied, which is explainable from the checklist the trader can see rather than
    from weights nobody chose.
    """

    if isinstance(candidate, Candidate):
        # The trend engine ranks by how complete the checklist is; it has no 0-100 total.
        return (-float(_conditions_met(candidate)), candidate.symbol)
    return (-(candidate.total or 0.0), candidate.symbol)


def pair_candidates(
    run: AnalysisRun, *, long_only: bool = False, review_within: int | None = None
) -> tuple[Candidate, ...]:
    """The coins worth a model's opinion, closest to a complete checklist first.

    ``review_within`` is the floor that replaced the retired 0-100 score: a coin reaches the
    analyst when its checklist is within that many conditions of complete. Zero reviews only
    fully-ready setups; one or two also admits the near-misses, which is where a model earns
    its keep — it can see a condition about to complete, or a context that makes a ticked box
    meaningless, and the detector can do neither. ``None`` keeps every surfaced coin.

    Drawn from every analysis rather than from ``run.setups``, because ``setups`` holds only
    the READY ones and a near-miss is exactly what this floor exists to admit.

    ``long_only`` drops shorts, because a trader who cannot take them should not be paying
    for opinions on them.
    """

    eligible = [
        Candidate(analysis=analysis)
        for analysis in run.analyses
        if analysis.direction is not Direction.NONE
        and analysis.plan is not None
        and _within_review_floor(analysis, review_within)
        and (not long_only or analysis.direction is Direction.LONG)
    ]
    # Closest to complete first, so a per-run cap spends the budget on the best candidates.
    return tuple(
        sorted(eligible, key=lambda c: (-_conditions_met(c), c.symbol))
    )


def _within_review_floor(analysis: CoinAnalysis, review_within: int | None) -> bool:
    """Whether the coin's checklist is near enough to complete to be worth an opinion.

    A coin with no verdict at all — the catalogue switched off — passes: there is no
    checklist to be near, and the alternative would be silently sending the model nothing.
    A coin with a verdict but no matched setup fails: it is not this method's trade.
    """

    verdict = analysis.verdict
    if verdict is None:
        return True
    if verdict.strategy is None:
        return False
    if review_within is None:
        return True
    return verdict.conditions_evaluated - verdict.conditions_met <= review_within


def _conditions_met(candidate: Candidate) -> int:
    """How many checklist rows the candidate satisfies; zero when it has no verdict."""

    verdict = candidate.analysis.verdict
    return verdict.conditions_met if verdict is not None else 0


def select(
    candidates: Sequence[S],
    *,
    min_score: float | None,
    max_candidates: int,
    cooldown: CooldownState | None = None,
    reference_timeframe: str | None = None,
    reserve_per_direction: int = 0,
) -> tuple[S, ...]:
    """Select the candidates worth reviewing, highest engine total first.

    Anything below ``min_score`` is dropped outright — the cap never promotes a
    below-floor setup into the selection just because capacity remains. When a
    ``cooldown`` and a ``reference_timeframe`` are supplied, anything already reviewed in
    the same direction on the same reference candle is dropped next; dropping before the
    ordering and the cap means a cooling setup frees its slot for the next-best fresh one
    rather than wasting it. The survivors are ordered by engine total descending and
    truncated to ``max_candidates``. Ties are broken by symbol so the selection is
    deterministic across runs over the same data.

    ``reserve_per_direction`` guarantees each direction that many slots before the rest are
    filled by score (see :func:`_select_with_reserve`); ``0`` disables it and selects purely
    by rank.

    Pure: neither the input sequence nor the cooldown state is mutated. Recording the
    selection is the caller's job (see :func:`record_selection`), which keeps "what would
    be reviewed" separable from "what was reviewed".
    """

    eligible = [
        candidate
        for candidate in candidates
        if min_score is None or candidate.total is None or candidate.total >= min_score
    ]

    if cooldown is not None and reference_timeframe is not None:
        eligible = [
            candidate
            for candidate in eligible
            if not _is_cooling(candidate, cooldown, reference_timeframe)
        ]

    eligible.sort(key=_rank_key)

    if reserve_per_direction > 0:
        return _select_with_reserve(eligible, max_candidates, reserve_per_direction)
    return tuple(eligible[:max_candidates])


def _select_with_reserve(
    eligible: list[S], max_candidates: int, reserve_per_direction: int
) -> tuple[S, ...]:
    """Guarantee each direction a share of the slots before filling the rest by score.

    Ranking purely by engine total means a lopsided market spends the entire review budget
    on one side. That is not a cosmetic problem: a basket of same-direction positions on
    correlated assets is one position, and it is the pattern behind the engine's 75%
    historical drawdown. Reserving slots per direction forces the analyst to look at the
    minority side, which is exactly where it can say something the engine's ranking cannot.

    Slots are dealt round-robin — best long, best short, second-best long, … — for
    ``reserve_per_direction`` rounds, so the guarantee degrades gracefully when the cap is
    smaller than both reservations combined. Any remaining capacity is filled by score, and
    the result is returned in score order.
    """

    by_direction: dict[Direction, list[S]] = {}
    for candidate in eligible:
        by_direction.setdefault(candidate.direction, []).append(candidate)

    chosen: list[S] = []
    seen: set[int] = set()

    def take(candidate: S) -> None:
        chosen.append(candidate)
        seen.add(id(candidate))

    # Deal one per direction per round, in a fixed direction order so the selection is
    # deterministic across runs over the same data.
    for position in range(reserve_per_direction):
        for direction in (Direction.LONG, Direction.SHORT):
            bucket = by_direction.get(direction, ())
            if len(chosen) >= max_candidates:
                break
            if position < len(bucket):
                take(bucket[position])

    # Fill whatever is left with the best remaining candidates regardless of direction.
    for candidate in eligible:
        if len(chosen) >= max_candidates:
            break
        if id(candidate) not in seen:
            take(candidate)

    chosen.sort(key=_rank_key)
    return tuple(chosen)


def _is_cooling(
    candidate: Selectable, cooldown: CooldownState, reference_timeframe: str
) -> bool:
    """Whether this candidate was already reviewed on the current reference candle."""
    key = candidate.cooldown_key(reference_timeframe)
    return key is not None and cooldown.is_cooling(key)


def record_selection(
    cooldown: CooldownState,
    selected: Sequence[Selectable],
    reference_timeframe: str,
) -> None:
    """Record every selected candidate against the reference candle it was selected on.

    Called after a selection is actually acted on, so the same coin and direction is not
    selected again until that timeframe closes a new candle. A candidate that cannot be
    keyed is skipped and simply remains eligible next time.
    """

    for candidate in selected:
        key = candidate.cooldown_key(reference_timeframe)
        if key is not None:
            cooldown.record(key)

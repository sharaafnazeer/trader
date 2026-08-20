"""Tests for AI-analyst candidate selection.

This is the module that bounds what a run ever costs, so the score floor, the per-run cap
and their interaction are pinned down precisely. Candidates are hand-built; nothing is
fetched and nothing is scored here.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trader.candidate_gate import (
    Candidate,
    CooldownKey,
    CooldownState,
    Selectable,
    pair_candidates,
    record_selection,
    select,
)
from trader.direction import Direction
from trader.market_data import Candles, OrderBook
from trader.runner import AnalysisRun, CoinAnalysis
from trader.trade_planner import TradePlan

REFERENCE_TF = "4h"


def _candles(symbol: str, timeframe: str = REFERENCE_TF, bar_open_ms: int = 1_000) -> Candles:
    frame = pd.DataFrame(
        [[bar_open_ms, 1.0, 1.0, 1.0, 1.0, 1.0]],
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


def _order_book(symbol: str) -> OrderBook:
    return OrderBook(
        symbol=symbol, best_bid=99.5, best_ask=100.5, bid_depth=10.0, ask_depth=10.0
    )


# Surfacing turns on having a plan, so the tests need one; its levels are irrelevant here.
_PLAN = TradePlan(
    direction=Direction.LONG,
    entry=100.0,
    stop_loss=95.0,
    take_profit=110.0,
    risk_reward=2.0,
    invalidation=96.0,
)


def _analysis(
    symbol: str,
    direction: Direction = Direction.LONG,
    *,
    bar_open_ms: int = 1_000,
    candles_by_tf: dict[str, Candles] | None = None,
    total: float | None = None,
) -> CoinAnalysis:
    """A per-coin record.

    ``total`` is a leftover knob from when candidates carried a 0-100 score; it now only
    decides whether the coin gets a trade plan, which is what surfacing turns on.
    """

    return CoinAnalysis(
        symbol=symbol,
        direction=direction,
        features_by_tf={},
        structure_by_tf={},
        candles_by_tf=(
            candles_by_tf
            if candles_by_tf is not None
            else {REFERENCE_TF: _candles(symbol, bar_open_ms=bar_open_ms)}
        ),
        order_book=_order_book(symbol),
        plan=_PLAN if total is not None else None,
    )


@dataclass(frozen=True, eq=False)
class _Scored:
    """A candidate carrying a 0-100 total, the shape the momentum scanner still produces.

    The trend engine's candidates lost their total when the quality score was retired, but
    ``select`` serves both scanners — so its score floor, its ordering and its per-direction
    reservation are exercised through this rather than through :class:`Candidate`, which
    would report ``None`` and make every one of those tests vacuous.
    """

    analysis: CoinAnalysis
    total: float | None

    @property
    def symbol(self) -> str:
        return self.analysis.symbol

    @property
    def direction(self) -> Direction:
        return self.analysis.direction

    def cooldown_key(self, reference_timeframe: str) -> CooldownKey | None:
        candles = self.analysis.candles_by_tf.get(reference_timeframe)
        if candles is None:
            return None
        return (self.symbol, self.direction, candles.latest_open_time)


def _candidate(
    symbol: str,
    total: float | None = 1.0,
    *,
    direction: Direction = Direction.LONG,
    bar_open_ms: int = 1_000,
    candles_by_tf: dict[str, Candles] | None = None,
) -> _Scored:
    return _Scored(
        analysis=_analysis(
            symbol, direction, bar_open_ms=bar_open_ms, candles_by_tf=candles_by_tf,
            total=total,
        ),
        total=total,
    )


def _symbols(candidates: tuple[Selectable, ...]) -> list[str]:
    return [candidate.symbol for candidate in candidates]


def test_pair_candidates_wraps_each_surfaced_coin() -> None:
    surfaced = (_analysis("ETHUSDT", total=90.0), _analysis("SOLUSDT", total=80.0))
    run = AnalysisRun(analyses=surfaced, setups=surfaced)

    paired = pair_candidates(run)

    # The run's own order is preserved: there is no score to rank by any more.
    assert _symbols(paired) == ["ETHUSDT", "SOLUSDT"]
    assert all(c.symbol == c.analysis.symbol for c in paired)


def test_pair_candidates_reports_no_total() -> None:
    """The 0-100 score was retired, so a candidate must not pretend to carry one."""
    surfaced = (_analysis("ETHUSDT", total=90.0),)

    paired = pair_candidates(AnalysisRun(analyses=surfaced, setups=surfaced))

    assert paired[0].total is None


def test_pair_candidates_takes_the_surfaced_list_not_every_analysis() -> None:
    # A coin that resolved no direction, or produced no plan, is not in ``setups`` and so
    # is not worth an opinion either.
    surfaced = (_analysis("SOLUSDT", total=80.0),)
    run = AnalysisRun(
        analyses=(*surfaced, _analysis("NODIRUSDT", Direction.NONE)), setups=surfaced
    )

    assert _symbols(pair_candidates(run)) == ["SOLUSDT"]


def test_pair_candidates_drops_shorts_when_the_trader_is_long_only() -> None:
    # Paying a model for an opinion on a trade that cannot be taken is pure waste.
    surfaced = (
        _analysis("LONGUSDT", Direction.LONG, total=70.0),
        _analysis("SHORTUSDT", Direction.SHORT, total=95.0),
    )
    run = AnalysisRun(analyses=surfaced, setups=surfaced)

    assert _symbols(pair_candidates(run, long_only=True)) == ["LONGUSDT"]
    assert _symbols(pair_candidates(run)) == ["LONGUSDT", "SHORTUSDT"]


def test_selection_takes_the_highest_scoring_candidates_up_to_the_cap() -> None:
    candidates = [
        _candidate("AAAUSDT", 81.0),
        _candidate("BBBUSDT", 95.0),
        _candidate("CCCUSDT", 88.0),
        _candidate("DDDUSDT", 99.0),
        _candidate("EEEUSDT", 84.0),
        _candidate("FFFUSDT", 91.0),
        _candidate("GGGUSDT", 86.0),
        _candidate("HHHUSDT", 93.0),
    ]

    selected = select(candidates, min_score=80.0, max_candidates=3)

    assert _symbols(selected) == ["DDDUSDT", "BBBUSDT", "HHHUSDT"]


def test_selection_is_ordered_by_engine_total_descending() -> None:
    candidates = [
        _candidate("AAAUSDT", 81.0),
        _candidate("BBBUSDT", 95.0),
        _candidate("CCCUSDT", 88.0),
    ]

    selected = select(candidates, min_score=0.0, max_candidates=10)

    assert [c.total for c in selected] == [95.0, 88.0, 81.0]


def test_a_below_floor_candidate_is_never_selected_even_with_spare_capacity() -> None:
    candidates = [_candidate("AAAUSDT", 79.9), _candidate("BBBUSDT", 95.0)]

    selected = select(candidates, min_score=80.0, max_candidates=10)

    assert _symbols(selected) == ["BBBUSDT"]


def test_a_candidate_exactly_on_the_floor_is_selected() -> None:
    selected = select([_candidate("AAAUSDT", 80.0)], min_score=80.0, max_candidates=10)

    assert _symbols(selected) == ["AAAUSDT"]


def test_ties_break_on_symbol_so_selection_is_deterministic() -> None:
    candidates = [
        _candidate("ZZZUSDT", 90.0),
        _candidate("AAAUSDT", 90.0),
        _candidate("MMMUSDT", 90.0),
    ]

    first = select(candidates, min_score=0.0, max_candidates=2)
    second = select(list(reversed(candidates)), min_score=0.0, max_candidates=2)

    assert _symbols(first) == ["AAAUSDT", "MMMUSDT"]
    assert _symbols(first) == _symbols(second)


def test_selection_does_not_mutate_the_candidates_passed_to_it() -> None:
    candidates = [
        _candidate("AAAUSDT", 81.0),
        _candidate("BBBUSDT", 95.0),
        _candidate("CCCUSDT", 88.0),
    ]
    before = list(candidates)

    select(candidates, min_score=0.0, max_candidates=1)

    assert candidates == before


def test_no_candidates_selects_nothing() -> None:
    assert select([], min_score=0.0, max_candidates=5) == ()


def test_everything_below_the_floor_selects_nothing() -> None:
    candidates = [_candidate("AAAUSDT", 10.0), _candidate("BBBUSDT", 20.0)]

    assert select(candidates, min_score=80.0, max_candidates=5) == ()


# --- Direction reservation ------------------------------------------------------


def _mixed() -> list[Candidate]:
    """A lopsided market: many strong shorts, a few weaker longs. The real shape."""
    shorts = [
        _candidate("FILUSDT", 79.2, direction=Direction.SHORT),
        _candidate("CHZUSDT", 79.1, direction=Direction.SHORT),
        _candidate("PYTHUSDT", 72.5, direction=Direction.SHORT),
        _candidate("GALAUSDT", 72.4, direction=Direction.SHORT),
        _candidate("TAOUSDT", 70.8, direction=Direction.SHORT),
    ]
    longs = [
        _candidate("TRXUSDT", 66.2, direction=Direction.LONG),
        _candidate("ZECUSDT", 62.6, direction=Direction.LONG),
        _candidate("PUMPUSDT", 61.7, direction=Direction.LONG),
        _candidate("BOMEUSDT", 51.7, direction=Direction.LONG),
    ]
    return shorts + longs


def test_without_reservation_a_lopsided_market_takes_the_whole_budget() -> None:
    selected = select(_mixed(), min_score=0.0, max_candidates=5, reserve_per_direction=0)

    # This is the failure mode: five shorts, no long ever looked at.
    assert {c.direction for c in selected} == {Direction.SHORT}


def test_reservation_guarantees_the_minority_direction_gets_slots() -> None:
    selected = select(_mixed(), min_score=0.0, max_candidates=5, reserve_per_direction=2)

    symbols = _symbols(selected)
    longs = [c for c in selected if c.direction is Direction.LONG]
    assert len(longs) == 2
    assert _symbols(tuple(longs)) == ["TRXUSDT", "ZECUSDT"]
    # The two best shorts are still reviewed, and the spare slot goes to the next best.
    assert "FILUSDT" in symbols and "CHZUSDT" in symbols
    assert "PYTHUSDT" in symbols


def test_the_reserved_selection_is_returned_in_score_order() -> None:
    selected = select(_mixed(), min_score=0.0, max_candidates=5, reserve_per_direction=2)

    totals = [c.total for c in selected]
    assert totals == sorted(totals, reverse=True)


def test_reservation_never_exceeds_the_cap() -> None:
    # Reserving 3 of each would be 6, above a cap of 5: the cap still wins.
    selected = select(_mixed(), min_score=0.0, max_candidates=5, reserve_per_direction=3)

    assert len(selected) == 5


def test_reservation_degrades_by_round_robin_when_the_cap_is_tight() -> None:
    # Cap 3, reserve 2: rounds deal best-long, best-short, second-best-long.
    selected = select(_mixed(), min_score=0.0, max_candidates=3, reserve_per_direction=2)

    assert len(selected) == 3
    assert len([c for c in selected if c.direction is Direction.LONG]) == 2
    assert len([c for c in selected if c.direction is Direction.SHORT]) == 1


def test_reservation_does_not_invent_candidates_for_an_absent_direction() -> None:
    only_shorts = [c for c in _mixed() if c.direction is Direction.SHORT]

    selected = select(only_shorts, min_score=0.0, max_candidates=5, reserve_per_direction=2)

    assert len(selected) == 5
    assert {c.direction for c in selected} == {Direction.SHORT}


def test_reservation_still_respects_the_score_floor() -> None:
    # BOME at 51.7 is the 4th-best long; a floor of 60 excludes it even with reservation.
    selected = select(_mixed(), min_score=60.0, max_candidates=8, reserve_per_direction=4)

    assert "BOMEUSDT" not in _symbols(selected)


def test_reaching_a_low_ranked_long_needs_both_a_low_floor_and_headroom() -> None:
    # BOME is the 4th-best long. Reservation alone cannot reach it inside a cap of 5:
    # round-robin deals L1,S1,L2,S2,L3 and stops.
    tight = select(_mixed(), min_score=0.0, max_candidates=5, reserve_per_direction=4)
    assert "BOMEUSDT" not in _symbols(tight)

    # It takes 4 reserved long slots *and* a cap with room for them.
    roomy = select(_mixed(), min_score=0.0, max_candidates=8, reserve_per_direction=4)
    assert "BOMEUSDT" in _symbols(roomy)


def test_reservation_composes_with_the_cooldown() -> None:
    cooldown = CooldownState()
    candidates = _mixed()

    first = select(
        candidates,
        min_score=0.0,
        max_candidates=5,
        reserve_per_direction=2,
        cooldown=cooldown,
        reference_timeframe=REFERENCE_TF,
    )
    record_selection(cooldown, first, REFERENCE_TF)
    second = select(
        candidates,
        min_score=0.0,
        max_candidates=5,
        reserve_per_direction=2,
        cooldown=cooldown,
        reference_timeframe=REFERENCE_TF,
    )

    # Everything already reviewed is cooling, so the next pass reaches further down —
    # and still honours the per-direction guarantee among what is left.
    assert not set(_symbols(first)) & set(_symbols(second))
    assert Direction.LONG in {c.direction for c in second}


# --- Cooldown -------------------------------------------------------------------


def _select(
    candidates: list[Candidate], cooldown: CooldownState, **overrides: object
) -> tuple[Candidate, ...]:
    kwargs: dict[str, object] = {
        "min_score": 0.0,
        "max_candidates": 10,
        "cooldown": cooldown,
        "reference_timeframe": REFERENCE_TF,
    }
    kwargs.update(overrides)
    return select(candidates, **kwargs)  # type: ignore[arg-type]


def test_a_candidate_reviewed_on_the_current_candle_is_not_selected_again() -> None:
    cooldown = CooldownState()
    candidates = [_candidate("SOLUSDT", 90.0, bar_open_ms=1_000)]

    first = _select(candidates, cooldown)
    record_selection(cooldown, first, REFERENCE_TF)
    second = _select(candidates, cooldown)

    assert _symbols(first) == ["SOLUSDT"]
    assert second == ()


def test_a_new_reference_candle_makes_the_candidate_eligible_again() -> None:
    cooldown = CooldownState()
    on_first_candle = [_candidate("SOLUSDT", 90.0, bar_open_ms=1_000)]
    on_next_candle = [_candidate("SOLUSDT", 90.0, bar_open_ms=2_000)]

    record_selection(cooldown, _select(on_first_candle, cooldown), REFERENCE_TF)

    assert _symbols(_select(on_next_candle, cooldown)) == ["SOLUSDT"]


def test_the_opposite_direction_is_eligible_immediately_on_the_same_candle() -> None:
    cooldown = CooldownState()
    as_long = [_candidate("SOLUSDT", 90.0, direction=Direction.LONG, bar_open_ms=1_000)]
    as_short = [_candidate("SOLUSDT", 90.0, direction=Direction.SHORT, bar_open_ms=1_000)]

    record_selection(cooldown, _select(as_long, cooldown), REFERENCE_TF)

    assert _symbols(_select(as_short, cooldown)) == ["SOLUSDT"]


def test_the_cooldown_is_keyed_on_the_configured_reference_timeframe() -> None:
    # The 4h candle has moved on; the 1d candle has not. Which one is consulted decides
    # whether the coin is eligible again.
    def candidate(four_hour_bar: int, daily_bar: int) -> Candidate:
        return _candidate(
            "SOLUSDT",
            90.0,
            candles_by_tf={
                "4h": _candles("SOLUSDT", "4h", four_hour_bar),
                "1d": _candles("SOLUSDT", "1d", daily_bar),
            },
        )

    first_poll = candidate(four_hour_bar=1_000, daily_bar=5_000)
    second_poll = candidate(four_hour_bar=2_000, daily_bar=5_000)

    keyed_on_4h = CooldownState()
    record_selection(keyed_on_4h, _select([first_poll], keyed_on_4h), "4h")
    keyed_on_1d = CooldownState()
    record_selection(
        keyed_on_1d, _select([first_poll], keyed_on_1d, reference_timeframe="1d"), "1d"
    )

    assert _symbols(_select([second_poll], keyed_on_4h)) == ["SOLUSDT"]
    assert _select([second_poll], keyed_on_1d, reference_timeframe="1d") == ()


def test_selection_does_not_mutate_the_cooldown_state_passed_to_it() -> None:
    cooldown = CooldownState()
    candidates = [_candidate("SOLUSDT", 90.0), _candidate("ETHUSDT", 85.0)]

    before = cooldown.snapshot()
    selected = _select(candidates, cooldown)
    after = cooldown.snapshot()

    assert selected != ()
    assert after == before == {}


def test_recording_is_what_writes_to_the_cooldown_state() -> None:
    cooldown = CooldownState()
    candidates = [_candidate("SOLUSDT", 90.0, bar_open_ms=1_000)]

    record_selection(cooldown, _select(candidates, cooldown), REFERENCE_TF)

    assert cooldown.snapshot() == {("SOLUSDT", Direction.LONG): 1_000}


def test_a_cooling_candidate_frees_its_slot_for_the_next_best_fresh_one() -> None:
    cooldown = CooldownState()
    strongest = _candidate("AAAUSDT", 95.0)
    runner_up = _candidate("BBBUSDT", 90.0)

    record_selection(cooldown, _select([strongest], cooldown, max_candidates=1), REFERENCE_TF)
    second_poll = _select([strongest, runner_up], cooldown, max_candidates=1)

    # The cap is 1 and the highest-scoring coin is cooling: the slot goes to the next
    # eligible candidate rather than being wasted.
    assert _symbols(second_poll) == ["BBBUSDT"]


def test_a_candidate_without_the_reference_timeframe_stays_eligible() -> None:
    cooldown = CooldownState()
    candidates = [
        _candidate(
            "SOLUSDT", 90.0, candles_by_tf={"1h": _candles("SOLUSDT", "1h", 1_000)}
        )
    ]

    record_selection(cooldown, _select(candidates, cooldown), REFERENCE_TF)

    # It could not be keyed, so nothing was recorded and it remains selectable — an
    # occasional duplicate beats silently suppressing a review.
    assert cooldown.snapshot() == {}
    assert _symbols(_select(candidates, cooldown)) == ["SOLUSDT"]


def test_no_cooldown_supplied_means_no_cooldown_filtering() -> None:
    candidates = [_candidate("SOLUSDT", 90.0)]

    assert _symbols(select(candidates, min_score=0.0, max_candidates=10)) == ["SOLUSDT"]

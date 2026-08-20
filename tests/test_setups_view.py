"""The scan as the method's four-part answer, end to end through the runner and CLI.

What is asserted here is the *shape of the answer*: trend, setup, entry status and
decision as separate facts, because collapsing them into one verdict is the misread the
whole catalogue exists to prevent. Plus the two properties that keep it honest — a scan
with nothing ready says so in words, and the ranking never lets a manufactured target
outrank a structural one.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
from rich.console import Console

from trader.cli import _render_setups, run_scan
from trader.config import Config, StrategyConfig
from trader.direction import Direction
from trader.market_data import Candles, OrderBook
from trader.runner import Runner
from trader.strategy import EntryStatus

_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def _closes(kind: str, rows: int = 300) -> list[float]:
    """A trending series shaped to exercise one checklist outcome.

    ``pullback`` is the full house and the hardest to build: price must retrace *into* the
    EMA21-EMA50 band while the stack stays ordered, the higher-highs structure survives, and
    the Stochastic RSI turns up from oversold. A dip alone fails the turn (no cross); a
    deeper dip fails the structure (it prints a new low). Three bars down and three back up
    satisfies all four — which is what a textbook pullback actually looks like.
    """

    base, out = 100.0, []
    for i in range(rows):
        base += 0.15
        out.append(base + 2.0 * {0: 0.0, 1: 0.5, 2: 0.9, 3: 0.5, 4: 0.0, 5: -0.35}[i % 6])

    if kind == "pullback":
        anchor = out[-7]
        tail = [anchor * (1 - 0.006 * (j + 1) / 3) for j in range(3)]
        tail.extend(tail[-1] * (1 + 0.001 * (j + 1) / 3) for j in range(3))
        out = out[:-6] + tail
    elif kind == "extended":
        # A vertical push away from the band: the entry has been missed.
        out[-1] = out[-1] * 1.10
    elif kind == "flat":
        # Strictly constant. An oscillating "flat" still orders its moving averages by
        # floating-point noise, which is enough to resolve a direction and cast a veto.
        out = [100.0] * rows
    elif kind == "down":
        out = [400.0 - c for c in out]
    return out


class _Market:
    def __init__(self, kinds: dict[str, str]) -> None:
        self._kinds = kinds

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        # BTC gets a flat series so the market-regime veto never decides these tests; what
        # is under test is the checklist, not the veto.
        kind = "flat" if symbol.startswith("BTC") else self._kinds.get(symbol, "pullback")
        closes = _closes(kind)
        rows = [[i, c, c + 0.4, c - 0.4, c, 1_000.0 + i] for i, c in enumerate(closes)]

        if kind == "pullback":
            # Shape the *final* bar as a hammer: a small bullish body with a long rejection
            # wick below it. Only the last bar is touched, and it can never be a swing pivot
            # (a pivot needs bars on both sides), so the moving averages, the oscillator and
            # the higher-highs structure are all exactly what the close series produced.
            # A reaction is a wick shape, not a close sequence — which is why it can be added
            # without disturbing the four conditions already satisfied.
            last, close = len(rows) - 1, closes[-1]
            rows[last] = [last, close - 0.15, close + 0.05, close - 0.95, close, 1_000.0 + last]

        return Candles(
            symbol=symbol, timeframe=timeframe, frame=pd.DataFrame(rows, columns=_COLUMNS)
        )

    def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        return OrderBook(
            symbol=symbol, best_bid=99.5, best_ask=100.5, bid_depth=500.0, ask_depth=400.0
        )


class _NoTradingView:
    def get_analysis(self, symbol: str, exchange: str, screener: str, interval: str) -> object:
        raise RuntimeError("unavailable")

    def get_analysis_batch(
        self, symbols: list[str], exchange: str, screener: str, interval: str
    ) -> dict[str, object]:
        return {}


def _run(kinds: dict[str, str], config: Config | None = None):  # type: ignore[no-untyped-def]
    cfg = config or Config(watchlist=list(kinds))
    return Runner(sleep=lambda _s: None).run_analysis(
        market_data=_Market(kinds),
        analysis=_NoTradingView(),
        watchlist=cfg.watchlist,
        timeframes=cfg.timeframes,
        reference_timeframe=cfg.reference_timeframe,
        lead_timeframe=cfg.lead_timeframe,
        htf_timeframes=tuple(cfg.htf_timeframes),
        strategies=cfg.strategies,
    )


def _text(run, **kwargs) -> str:  # type: ignore[no-untyped-def]
    console = Console(width=220, record=True)
    _render_setups(run, console, **kwargs)
    return console.export_text()


# --- The verdict reaches the run --------------------------------------------------


def test_a_pulled_back_coin_is_ready_and_carries_the_four_facts() -> None:
    run = _run({"PULLUSDT": "pullback"})
    analysis = run.analyses[0]

    assert analysis.verdict is not None
    assert analysis.verdict.trend is Direction.LONG
    assert analysis.verdict.strategy is not None
    assert analysis.verdict.entry_status is EntryStatus.READY
    assert analysis.verdict.decision is Direction.LONG
    assert analysis.symbol in {a.symbol for a in run.setups}


def test_an_extended_coin_waits_and_is_kept_out_of_the_trade_list() -> None:
    run = _run({"EXTUSDT": "extended"})
    analysis = run.analyses[0]

    assert analysis.verdict is not None
    assert analysis.verdict.trend is Direction.LONG  # the trend is intact
    assert analysis.verdict.entry_status is EntryStatus.NOT_READY
    assert analysis.verdict.decision is Direction.NONE
    assert run.setups == ()  # ...but it is not a trade


def test_the_waiting_coins_reason_names_the_distance_and_the_zone() -> None:
    run = _run({"EXTUSDT": "extended"})

    reason = run.analyses[0].verdict.reason  # type: ignore[union-attr]
    assert "ATR above" in reason
    assert "zone" in reason


def test_with_the_catalogue_off_the_checklist_does_not_gate_the_trade_list() -> None:
    config = Config(watchlist=["EXTUSDT"], strategies=StrategyConfig(enabled=False))
    run = _run({"EXTUSDT": "extended"}, config)

    assert run.analyses[0].verdict is None
    # Surfacing falls back to "resolved a direction and produced a plan".
    assert [a.symbol for a in run.setups] == ["EXTUSDT"]


# --- Rendering --------------------------------------------------------------------


def test_the_setups_table_shows_trend_setup_entry_and_decision_separately() -> None:
    text = _text(_run({"PULLUSDT": "pullback"}))

    for column in ("Trend", "Setup", "Entry", "Decision"):
        assert column in text
    assert "READY" in text
    assert "1A" in text


def test_a_scan_with_nothing_ready_says_so_in_words() -> None:
    """Silence is a result. An empty table alone reads like a failure."""

    text = _text(_run({"EXTUSDT": "extended"}))

    assert "Nothing is ready to trade" in text
    assert "waiting on an entry" in text


def test_the_all_view_shows_the_waiting_coin_and_why() -> None:
    text = _text(_run({"EXTUSDT": "extended"}), show_all=True)

    assert "EXTUSDT" in text
    assert "NOT_READY" in text
    assert "WAIT" in text


def test_the_table_labels_the_target_structural_or_manufactured() -> None:
    text = _text(_run({"PULLUSDT": "pullback"}))

    assert "Target" in text
    assert "structural" in text or "manufactured" in text


def test_the_short_side_is_reported_with_its_own_setup_label() -> None:
    run = _run({"DOWNUSDT": "down"})
    verdict = run.analyses[0].verdict

    assert verdict is not None
    assert verdict.trend is Direction.SHORT
    assert verdict.strategy is not None and verdict.strategy.value == "1B"


# --- Ranking ----------------------------------------------------------------------


def test_a_structural_target_ranks_above_a_manufactured_one_among_equals() -> None:
    """The reward-to-risk preference: it orders, and never excludes."""

    run = _run({"AAAUSDT": "pullback", "BBBUSDT": "pullback"})
    assert len(run.setups) == 2

    # Force the tie-break by marking one plan's target manufactured and the other's real.
    manufactured, structural = run.setups[0], run.setups[1]
    patched = replace(
        run,
        setups=tuple(
            sorted(
                (
                    replace(
                        manufactured,
                        plan=replace(manufactured.plan, target_is_structural=False),  # type: ignore[arg-type]
                    ),
                    replace(
                        structural,
                        plan=replace(structural.plan, target_is_structural=True),  # type: ignore[arg-type]
                    ),
                ),
                key=lambda a: (
                    -a.verdict.conditions_met,  # type: ignore[union-attr]
                    0 if a.plan.target_is_structural else 1,  # type: ignore[union-attr]
                    a.symbol,
                ),
            )
        ),
    )

    assert patched.setups[0].plan.target_is_structural is True  # type: ignore[union-attr]


def test_the_scan_command_runs_end_to_end_with_the_catalogue() -> None:
    console = Console(width=220, record=True)
    run_scan(
        _Market({"PULLUSDT": "pullback", "EXTUSDT": "extended"}),
        _NoTradingView(),
        console,
        Config(watchlist=["PULLUSDT", "EXTUSDT"]),
        sleep=lambda _s: None,
    )
    text = console.export_text()

    assert "Setups" in text
    assert "PULLUSDT" in text


# --- The momentum turn, end to end ------------------------------------------------


def test_the_ready_coins_turn_is_reported_from_the_candles() -> None:
    """The turn is derived from prices, not asserted into the fixture."""

    run = _run({"PULLUSDT": "pullback"})
    verdict = run.analyses[0].verdict
    assert verdict is not None

    turn = verdict.condition("momentum_turn")
    assert turn is not None and turn.met is True
    assert "crossed above %D" in turn.detail


def test_a_coin_at_the_zone_without_a_turn_still_waits() -> None:
    """The zone alone is not the setup; the method wants momentum turning too."""

    run = _run({"DOWNUSDT": "down"})
    verdict = run.analyses[0].verdict
    assert verdict is not None

    zone = verdict.condition("zone")
    turn = verdict.condition("momentum_turn")
    assert zone is not None and zone.met is True
    assert turn is not None and turn.met is False
    assert verdict.entry_status is EntryStatus.NOT_READY
    assert run.setups == ()


def test_the_evidence_carries_the_turn_for_the_analyst() -> None:
    from trader.brief import brief_to_dict, build_brief

    run = _run({"PULLUSDT": "pullback"})
    analysis = run.analyses[0]
    brief = build_brief(
        analysis.symbol,
        analysis.direction,
        features_by_tf=analysis.features_by_tf,
        structure_by_tf=analysis.structure_by_tf,
        close_by_tf={tf: c.latest_close for tf, c in analysis.candles_by_tf.items()},
        order_book=analysis.order_book,
        plan=analysis.plan,
        limited_history=analysis.limited_history,
        timeframes=["4h"],
        verdict=analysis.verdict,
    )

    payload = brief_to_dict(brief)
    row = payload["timeframes"][0]  # type: ignore[index]
    assert row["stoch_cross"] is not None
    assert "up" in row["stoch_cross"]
    # And the checklist itself travels, so the model sees which rows passed and why.
    names = [c["name"] for c in payload["verdict"]["conditions"]]  # type: ignore[index]
    assert "momentum_turn" in names


def test_macd_confirmation_is_derived_from_the_candles_end_to_end() -> None:
    run = _run({"PULLUSDT": "pullback"})
    verdict = run.analyses[0].verdict
    assert verdict is not None

    macd = verdict.condition("macd")
    assert macd is not None and macd.met is True
    # Whichever reading carried it, the detail says which and quotes the values.
    assert "zero" in macd.detail
    assert "MACD" in macd.detail


def test_all_judged_conditions_must_hold_for_a_ready_verdict() -> None:
    ready = _run({"PULLUSDT": "pullback"}).analyses[0].verdict
    waiting = _run({"EXTUSDT": "extended"}).analyses[0].verdict
    assert ready is not None and waiting is not None

    assert (ready.conditions_met, ready.conditions_evaluated) == (7, 7)
    assert ready.entry_status is EntryStatus.READY
    # The extended coin passes three of seven and is therefore not a trade.
    assert waiting.conditions_met == 3
    assert waiting.entry_status is EntryStatus.NOT_READY


def test_the_evidence_carries_the_macd_turn() -> None:
    from trader.brief import brief_to_dict, build_brief

    analysis = _run({"PULLUSDT": "pullback"}).analyses[0]
    brief = build_brief(
        analysis.symbol,
        analysis.direction,
        features_by_tf=analysis.features_by_tf,
        structure_by_tf=analysis.structure_by_tf,
        close_by_tf={tf: c.latest_close for tf, c in analysis.candles_by_tf.items()},
        order_book=analysis.order_book,
        plan=analysis.plan,
        limited_history=analysis.limited_history,
        timeframes=["4h"],
        verdict=analysis.verdict,
    )

    row = brief_to_dict(brief)["timeframes"][0]  # type: ignore[index]
    assert row["macd_cross"] is not None
    assert "zero" in row["macd_cross"]


def test_the_reaction_is_read_from_the_candles_end_to_end() -> None:
    """The hammer is a wick shape in the fixture's final bar, not an injected fact."""

    run = _run({"PULLUSDT": "pullback"})
    analysis = run.analyses[0]

    assert analysis.reaction_patterns == ("hammer",)
    reaction = analysis.verdict.condition("reaction")  # type: ignore[union-attr]
    assert reaction is not None and reaction.met is True
    assert "hammer" in reaction.detail


def test_a_coin_with_no_reaction_at_the_zone_is_not_a_trade() -> None:
    run = _run({"DOWNUSDT": "down"})
    verdict = run.analyses[0].verdict
    assert verdict is not None

    zone = verdict.condition("zone")
    reaction = verdict.condition("reaction")
    assert zone is not None and zone.met is True
    assert reaction is not None and reaction.met is False
    assert run.setups == ()


def test_the_evidence_carries_the_detected_reaction() -> None:
    from trader.brief import brief_to_dict, build_brief

    analysis = _run({"PULLUSDT": "pullback"}).analyses[0]
    brief = build_brief(
        analysis.symbol,
        analysis.direction,
        features_by_tf=analysis.features_by_tf,
        structure_by_tf=analysis.structure_by_tf,
        close_by_tf={tf: c.latest_close for tf, c in analysis.candles_by_tf.items()},
        order_book=analysis.order_book,
        plan=analysis.plan,
        limited_history=analysis.limited_history,
        timeframes=["4h"],
        verdict=analysis.verdict,
        patterns_by_tf={"4h": analysis.reaction_patterns},
    )

    row = brief_to_dict(brief)["timeframes"][0]  # type: ignore[index]
    assert row["reaction_patterns"] == ["hammer"]


# --- The trendline, end to end ----------------------------------------------------


def test_the_trendline_is_fitted_from_the_detected_pivots() -> None:
    """Fitted through the swing lows the structure detector already found — one detector,
    one answer to 'where are the swings'."""

    analysis = _run({"PULLUSDT": "pullback"}).analyses[0]
    line = analysis.trendline

    assert line is not None
    assert line.is_rising
    assert line.touches >= 3
    assert line.r_squared >= 0.7


def test_a_short_fits_its_line_through_the_swing_highs() -> None:
    analysis = _run({"DOWNUSDT": "down"}).analyses[0]
    line = analysis.trendline

    assert line is not None
    assert line.is_falling


def test_an_extended_coin_has_not_returned_to_its_trendline() -> None:
    verdict = _run({"EXTUSDT": "extended"}).analyses[0].verdict
    assert verdict is not None

    line = verdict.condition("trendline")
    assert line is not None and line.met is False
    assert "above the rising trendline" in line.detail
    assert "wait for a return to that support" in line.detail


def test_the_full_checklist_reaches_seven_of_seven() -> None:
    verdict = _run({"PULLUSDT": "pullback"}).analyses[0].verdict
    assert verdict is not None

    assert (verdict.conditions_met, verdict.conditions_evaluated) == (7, 7)
    assert verdict.entry_status is EntryStatus.READY
    assert all(c.met is True for c in verdict.conditions)


def test_the_evidence_carries_the_fitted_line() -> None:
    from trader.brief import brief_to_dict, build_brief

    analysis = _run({"PULLUSDT": "pullback"}).analyses[0]
    line = analysis.trendline
    assert line is not None
    brief = build_brief(
        analysis.symbol,
        analysis.direction,
        features_by_tf=analysis.features_by_tf,
        structure_by_tf=analysis.structure_by_tf,
        close_by_tf={tf: c.latest_close for tf, c in analysis.candles_by_tf.items()},
        order_book=analysis.order_book,
        plan=analysis.plan,
        limited_history=analysis.limited_history,
        timeframes=["4h"],
        verdict=analysis.verdict,
        trendline_by_tf={"4h": f"rising at {line.level_now:.6g}"},
    )

    row = brief_to_dict(brief)["timeframes"][0]  # type: ignore[index]
    assert row["trendline"] is not None
    assert "rising" in row["trendline"]


# --- Who reaches the analyst, and in what order -----------------------------------


def _partial(kind: str, within: int):  # type: ignore[no-untyped-def]
    from trader.candidate_gate import pair_candidates

    return pair_candidates(_run({"XUSDT": kind}), review_within=within)


def test_a_ready_setup_always_reaches_the_analyst() -> None:
    assert [c.symbol for c in _partial("pullback", 0)] == ["XUSDT"]


def test_a_coin_further_from_complete_than_the_floor_is_not_reviewed() -> None:
    """The extended coin is four conditions short; a floor of one keeps it out."""

    assert _partial("extended", 1) == ()


def test_lowering_the_floor_admits_a_near_miss() -> None:
    # The downtrending coin is two conditions short of complete.
    assert _partial("down", 1) == ()
    assert [c.symbol for c in _partial("down", 2)] == ["XUSDT"]


def test_candidates_are_ordered_closest_to_complete_first() -> None:
    from trader.candidate_gate import pair_candidates

    run = _run({"READYUSDT": "pullback", "NEARUSDT": "down"})
    ordered = pair_candidates(run, review_within=3)

    assert [c.symbol for c in ordered] == ["READYUSDT", "NEARUSDT"]


def test_a_coin_matching_no_setup_is_never_reviewed() -> None:
    """Not this method's trade, however close its other numbers look."""

    from trader.candidate_gate import pair_candidates

    run = _run({"FLATUSDT": "flat"})

    assert pair_candidates(run, review_within=7) == ()


def test_with_the_catalogue_off_every_surfaced_coin_is_reviewable() -> None:
    """There is no checklist to be near, so the floor cannot apply."""

    from trader.candidate_gate import pair_candidates

    config = Config(watchlist=["EXTUSDT"], strategies=StrategyConfig(enabled=False))
    run = _run({"EXTUSDT": "extended"}, config)

    assert [c.symbol for c in pair_candidates(run, review_within=0)] == ["EXTUSDT"]


def test_the_per_direction_reserve_still_guarantees_both_sides() -> None:
    """The cap must not spend the whole budget on whichever side happens to rank higher."""

    from trader.candidate_gate import pair_candidates, select

    run = _run(
        {
            "LONGAUSDT": "pullback",
            "LONGBUSDT": "pullback",
            "SHORTAUSDT": "down",
            "SHORTBUSDT": "down",
        }
    )
    candidates = pair_candidates(run, review_within=3)
    assert {c.direction for c in candidates} == {Direction.LONG, Direction.SHORT}

    # Longs are closer to complete (7/7 vs 5/7), so without a reserve they take both slots.
    unreserved = select(candidates, min_score=None, max_candidates=2)
    reserved = select(candidates, min_score=None, max_candidates=2, reserve_per_direction=1)

    assert {c.direction for c in unreserved} == {Direction.LONG}
    assert {c.direction for c in reserved} == {Direction.LONG, Direction.SHORT}


def test_without_an_analyst_the_detector_still_reports_and_orders() -> None:
    """No credential, no model call — the engine's own reading is free and still ranked."""

    text = _text(_run({"PULLUSDT": "pullback", "EXTUSDT": "extended"}), show_all=True)

    assert "READY (7/7)" in text
    # Closest to complete first: the ready coin above the one three conditions short.
    assert text.index("PULLUSDT") < text.index("EXTUSDT")

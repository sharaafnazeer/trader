"""Tests for the AI-analyst evidence pack.

Everything here is built from hand-made features/structure/plans — no candles are fetched
and no model is contacted. The brief is the only thing an analyst ever sees about a coin,
so these tests pin down exactly what it carries and how it reads.
"""

from __future__ import annotations

import json

import pytest

from trader.brief import (
    EMA_STACK_BEARISH,
    EMA_STACK_BULLISH,
    EMA_STACK_MIXED,
    MarketBrief,
    brief_to_dict,
    build_brief,
    render_briefs,
)
from trader.direction import Direction
from trader.indicators import TimeframeFeatures
from trader.market_data import OrderBook
from trader.structure import Structure, StructureState
from trader.trade_planner import TradePlan


def _features(
    symbol: str = "SOLUSDT",
    timeframe: str = "4h",
    *,
    ema20: float = 120.0,
    ema50: float = 110.0,
    ema200: float = 100.0,
    atr: float = 2.5,
    relative_volume: float = 1.4,
    limited_history: bool = False,
) -> TimeframeFeatures:
    return TimeframeFeatures(
        symbol=symbol,
        timeframe=timeframe,
        ema10=ema20 + (ema20 - ema50) * 0.1,
        ema21=ema20,
        ema50=ema50,
        sma200=ema200,
        macd=1.0,
        macd_signal=0.5,
        macd_hist=0.5,
        atr=atr,
        volume=1_000.0,
        relative_volume=relative_volume,
        limited_history=limited_history,
    )


def _structure(
    structure: Structure = Structure.BULLISH,
    highs: tuple[float, ...] = (118.0, 125.0),
    lows: tuple[float, ...] = (95.0, 102.0),
) -> StructureState:
    return StructureState(structure=structure, swing_highs=highs, swing_lows=lows)


def _order_book(
    best_bid: float = 99.5,
    best_ask: float = 100.5,
    bid_depth: float = 500.0,
    ask_depth: float = 400.0,
) -> OrderBook:
    return OrderBook(
        symbol="SOLUSDT",
        best_bid=best_bid,
        best_ask=best_ask,
        bid_depth=bid_depth,
        ask_depth=ask_depth,
    )


# The trend brief no longer carries a total or a category breakdown — the 0-100 score was
# retired with the indicators behind it — so a candidate is now just a symbol and a
# direction.


def _plan() -> TradePlan:
    return TradePlan(
        direction=Direction.LONG,
        entry=100.0,
        stop_loss=94.0,
        take_profit=112.0,
        risk_reward=2.0,
        invalidation=95.0,
    )


def _build(
    *,
    timeframes: tuple[str, ...] = ("1h", "4h"),
    limited_history: bool = False,
    plan: TradePlan | None = None,
    features_overrides: dict[str, TimeframeFeatures] | None = None,
    structure_overrides: dict[str, StructureState] | None = None,
    symbol: str = "SOLUSDT",
    direction: Direction = Direction.LONG,
    order_book: OrderBook | None = None,
):
    features = {tf: _features(timeframe=tf) for tf in timeframes}
    features.update(features_overrides or {})
    structures = {tf: _structure() for tf in timeframes}
    structures.update(structure_overrides or {})
    return build_brief(
        symbol,
        direction,
        features_by_tf=features,
        structure_by_tf=structures,
        close_by_tf={tf: 100.0 for tf in timeframes},
        order_book=order_book if order_book is not None else _order_book(),
        plan=plan if plan is not None else _plan(),
        limited_history=limited_history,
        timeframes=timeframes,
    )


def test_brief_carries_the_engine_verdict_without_a_score() -> None:
    """The trend brief identifies the candidate; the 0-100 total and its category
    breakdown were retired with the indicators behind them, so both are absent rather
    than zeroed — a zero would read as an opinion the engine no longer holds."""

    brief = _build()

    assert brief.symbol == "SOLUSDT"
    assert brief.direction is Direction.LONG
    assert brief.total is None
    assert brief.categories == ()


def test_brief_carries_one_row_per_timeframe_in_configured_order() -> None:
    brief = _build(timeframes=("15m", "1h", "4h", "1d"))

    assert [t.timeframe for t in brief.timeframes] == ["15m", "1h", "4h", "1d"]


def test_timeframe_row_summarizes_trend_oscillators_and_structure() -> None:
    brief = _build(timeframes=("4h",))
    row = brief.timeframes[0]

    assert row.ema_stack == EMA_STACK_BULLISH
    # ATR 2.5 against a close of 100 is 2.5%.
    assert row.atr_pct == pytest.approx(2.5)
    assert row.relative_volume == pytest.approx(1.4)
    assert row.structure == "BULLISH"
    assert row.last_swing_high == pytest.approx(125.0)
    assert row.last_swing_low == pytest.approx(102.0)


def test_ema_stack_is_described_as_bearish_or_mixed_when_it_is() -> None:
    bearish = _build(
        timeframes=("4h",),
        features_overrides={"4h": _features(ema20=90.0, ema50=100.0, ema200=110.0)},
    )
    mixed = _build(
        timeframes=("4h",),
        features_overrides={"4h": _features(ema20=105.0, ema50=100.0, ema200=110.0)},
    )

    assert bearish.timeframes[0].ema_stack == EMA_STACK_BEARISH
    assert mixed.timeframes[0].ema_stack == EMA_STACK_MIXED


def test_brief_carries_the_engine_trade_plan() -> None:
    brief = _build()

    assert brief.plan is not None
    assert brief.plan.entry == pytest.approx(100.0)
    assert brief.plan.stop_loss == pytest.approx(94.0)
    assert brief.plan.take_profit == pytest.approx(112.0)
    assert brief.plan.risk_reward == pytest.approx(2.0)
    assert brief.plan.invalidation == pytest.approx(95.0)


def test_brief_tolerates_a_setup_with_no_usable_plan() -> None:
    features = {"4h": _features()}
    brief = build_brief(
        "SOLUSDT",
        Direction.LONG,
        features_by_tf=features,
        structure_by_tf={"4h": _structure()},
        close_by_tf={"4h": 100.0},
        order_book=_order_book(),
        plan=None,
        limited_history=False,
        timeframes=("4h",),
    )

    assert brief.plan is None
    assert "plan: none" in render_briefs([brief], MarketBrief())


def test_liquidity_is_relative_spread_and_quote_notional_depth() -> None:
    brief = _build(order_book=_order_book(best_bid=99.5, best_ask=100.5, bid_depth=500.0))

    # Mid is 100.0: a 1.0 spread is 1%, and 500 base units is 50_000 USDT of depth.
    assert brief.liquidity.spread_pct == pytest.approx(1.0)
    assert brief.liquidity.bid_depth_notional == pytest.approx(50_000.0)
    assert brief.liquidity.ask_depth_notional == pytest.approx(40_000.0)


def test_limited_history_marker_is_carried_and_stated_in_the_rendered_evidence() -> None:
    marked = _build(limited_history=True)
    unmarked = _build(limited_history=False)

    assert marked.limited_history is True
    assert "LIMITED" in render_briefs([marked], MarketBrief())
    assert "LIMITED" not in render_briefs([unmarked], MarketBrief())


def test_brief_holds_only_json_serializable_values() -> None:
    brief = _build()

    # allow_nan=False rejects the non-standard NaN/Infinity tokens, so this only passes
    # if every float in the brief is finite or has been normalized to null.
    encoded = json.dumps(brief_to_dict(brief), allow_nan=False)

    assert json.loads(encoded)["symbol"] == "SOLUSDT"


def test_non_finite_indicator_values_are_normalized_to_null() -> None:
    brief = _build(
        timeframes=("4h",),
        features_overrides={"4h": _features(atr=float("nan"))},
    )
    assert brief.timeframes[0].atr_pct is None
    json.dumps(brief_to_dict(brief), allow_nan=False)


def test_rendered_evidence_states_the_btc_regime_once() -> None:
    text = render_briefs([_build(), _build(symbol="ETHUSDT")], MarketBrief(
        btc_direction=Direction.LONG
    ))

    assert text.count("BTC regime: LONG") == 1


def test_rendered_evidence_lists_every_candidate_with_its_engine_verdict() -> None:
    briefs = [
        _build(symbol="SOLUSDT", direction=Direction.LONG),
        _build(symbol="ETHUSDT", direction=Direction.SHORT),
    ]

    text = render_briefs(briefs, MarketBrief())

    assert "CANDIDATES (2)" in text
    # No score clause: the trend engine has no total to print.
    assert "[1] SOLUSDT — engine LONG" in text
    assert "[2] ETHUSDT — engine SHORT" in text
    assert "score" not in text


def test_rendered_evidence_handles_an_empty_selection() -> None:
    text = render_briefs([], MarketBrief())

    assert "CANDIDATES (0)" in text
    assert "(none selected for review)" in text

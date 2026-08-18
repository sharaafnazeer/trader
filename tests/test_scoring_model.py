"""Unit tests for the multi-factor ScoringModel (pure, deterministic).

Features, structure, BTC context, and the aggregated TradingView value are hand-built
so every category fraction is pinned by its inputs, mirroring the boundary-focused
style of the direction and v1 scoring tests. No network, no candle fabrication.
"""

from __future__ import annotations

from trader.config import (
    CATEGORY_BREAKOUT,
    CATEGORY_BTC,
    CATEGORY_LIQUIDITY,
    CATEGORY_MOMENTUM,
    CATEGORY_RISK_REWARD,
    CATEGORY_STRUCTURE,
    CATEGORY_TREND,
    CATEGORY_VOLUME,
    DEFAULT_CATEGORY_WEIGHTS,
)
from trader.direction import Direction, MarketContext
from trader.indicators import TimeframeFeatures
from trader.market_data import OrderBook
from trader.scoring_model import (
    ScoringModel,
    _breakout_fraction,
    _liquidity_fraction,
    _volume_fraction,
    surface,
)
from trader.structure import Structure, StructureState

# All eight categories are implemented now, so a fully-confirming coin can earn the
# full 100 points.
IMPLEMENTED_MAX = 100.0

# Explicit liquidity gates used by the pinned-input tests so results don't depend on
# the in-code config defaults.
REL_VOL_MULTIPLE = 1.5
MIN_DEPTH = 50.0
MAX_SPREAD = 0.005
# A trade plan whose ratio meets the target so the Risk-to-reward category is full.
TARGET_RR = 2.0


def _features(*, bullish: bool) -> TimeframeFeatures:
    """Features that fully confirm (bullish) or fully contradict (bearish) a long."""

    if bullish:
        ema20, ema50, ema200 = 3.0, 2.0, 1.0
        rsi, macd, macd_signal, macd_hist, roc = 100.0, 1.0, 0.0, 1.0, 1.0
        obv_slope, relative_volume = 1.0, 2.0
    else:
        ema20, ema50, ema200 = 1.0, 2.0, 3.0
        rsi, macd, macd_signal, macd_hist, roc = 0.0, 0.0, 1.0, -1.0, -1.0
        obv_slope, relative_volume = -1.0, 1.0
    return TimeframeFeatures(
        symbol="X",
        timeframe="4h",
        ema20=ema20,
        ema50=ema50,
        ema200=ema200,
        rsi=rsi,
        macd=macd,
        macd_signal=macd_signal,
        macd_hist=macd_hist,
        roc=roc,
        atr=1.0,
        obv=0.0,
        obv_slope=obv_slope,
        bollinger_width=0.0,
        relative_volume=relative_volume,
    )


def _structure(*, bullish: bool) -> StructureState:
    state = Structure.BULLISH if bullish else Structure.BEARISH
    return StructureState(structure=state, swing_highs=(1.0, 2.0), swing_lows=(0.5, 1.0))


def _maps(*, bullish: bool) -> tuple[dict[str, TimeframeFeatures], dict[str, StructureState]]:
    features = {"4h": _features(bullish=bullish), "1d": _features(bullish=bullish)}
    structure = {"4h": _structure(bullish=bullish), "1d": _structure(bullish=bullish)}
    return features, structure


def _order_book(*, liquid: bool) -> OrderBook:
    """A deep, tight book (liquid) or a thin, wide one (illiquid)."""

    if liquid:
        return OrderBook("X", best_bid=100.0, best_ask=100.01, bid_depth=500.0, ask_depth=500.0)
    return OrderBook("X", best_bid=100.0, best_ask=105.0, bid_depth=1.0, ask_depth=1.0)


def test_fully_confirming_long_scores_near_the_implemented_maximum() -> None:
    features, structure = _maps(bullish=True)
    context = MarketContext(btc_direction=Direction.LONG)

    score = ScoringModel().score(
        "X",
        Direction.LONG,
        features,
        structure,
        context,
        DEFAULT_CATEGORY_WEIGHTS,
        tradingview=2.0,  # STRONG_BUY agrees with the long
        order_book=_order_book(liquid=True),
        # Close sits right at the last swing high -> a clean breakout.
        close_by_tf={"4h": 2.0, "1d": 2.0},
        relative_volume_multiple=REL_VOL_MULTIPLE,
        min_depth=MIN_DEPTH,
        max_spread=MAX_SPREAD,
        risk_reward=TARGET_RR,  # a plan meeting the target -> full R:R credit
        target_rr=TARGET_RR,
    )

    # Every implemented category earns full or near-full credit. Liquidity is capped
    # just under full because a real, tradeable book has a small non-zero spread, so
    # the total lands near (not exactly at) the 100 implemented points.
    assert score.total >= 0.97 * IMPLEMENTED_MAX
    assert {c.name for c in score.categories} == {
        CATEGORY_TREND,
        CATEGORY_STRUCTURE,
        CATEGORY_MOMENTUM,
        CATEGORY_VOLUME,
        CATEGORY_BREAKOUT,
        CATEGORY_BTC,
        CATEGORY_LIQUIDITY,
        CATEGORY_RISK_REWARD,
    }


def test_maximum_achievable_total_is_one_hundred() -> None:
    # A perfectly-confirming setup with an ideal (zero-spread, deep) book and a plan at
    # the target R:R earns full credit in every one of the eight categories -> 100.
    features, structure = _maps(bullish=True)
    context = MarketContext(btc_direction=Direction.LONG)
    ideal_book = OrderBook("X", best_bid=100.0, best_ask=100.0, bid_depth=500.0, ask_depth=500.0)

    score = ScoringModel().score(
        "X",
        Direction.LONG,
        features,
        structure,
        context,
        DEFAULT_CATEGORY_WEIGHTS,
        tradingview=2.0,
        order_book=ideal_book,
        close_by_tf={"4h": 2.0, "1d": 2.0},
        relative_volume_multiple=REL_VOL_MULTIPLE,
        min_depth=MIN_DEPTH,
        max_spread=MAX_SPREAD,
        risk_reward=TARGET_RR,
        target_rr=TARGET_RR,
    )

    assert score.total == 100.0
    assert all(c.fraction == 1.0 for c in score.categories)


def test_fully_contradicting_long_scores_at_most_five() -> None:
    features, structure = _maps(bullish=False)
    context = MarketContext(btc_direction=Direction.SHORT)

    score = ScoringModel().score(
        "X",
        Direction.LONG,
        features,
        structure,
        context,
        DEFAULT_CATEGORY_WEIGHTS,
        tradingview=-2.0,  # STRONG_SELL contradicts the long
        order_book=_order_book(liquid=False),
        # Close has broken below support -> no breakout credit.
        close_by_tf={"4h": 0.5, "1d": 0.5},
        relative_volume_multiple=REL_VOL_MULTIPLE,
        min_depth=MIN_DEPTH,
        max_spread=MAX_SPREAD,
    )

    assert score.total <= 5.0


def test_increasing_a_category_weight_increases_the_total() -> None:
    features, structure = _maps(bullish=True)
    context = MarketContext(btc_direction=Direction.LONG)
    model = ScoringModel()

    base = model.score(
        "X", Direction.LONG, features, structure, context, DEFAULT_CATEGORY_WEIGHTS, tradingview=2.0
    )

    heavier = dict(DEFAULT_CATEGORY_WEIGHTS)
    heavier[CATEGORY_TREND] = DEFAULT_CATEGORY_WEIGHTS[CATEGORY_TREND] + 25.0
    boosted = model.score(
        "X", Direction.LONG, features, structure, context, heavier, tradingview=2.0
    )

    # Trend earns credit here, so raising its weight raises the total.
    assert boosted.total > base.total


def test_none_direction_and_sub_threshold_coins_are_not_surfaced() -> None:
    features, structure = _maps(bullish=True)
    context = MarketContext(btc_direction=Direction.LONG)
    model = ScoringModel()

    strong = model.score(
        "STRONG", Direction.LONG, features, structure, context, DEFAULT_CATEGORY_WEIGHTS,
        tradingview=2.0,
    )  # total 65
    weak_features, weak_structure = _maps(bullish=False)
    weak = model.score(
        "WEAK", Direction.LONG, weak_features, weak_structure,
        MarketContext(btc_direction=Direction.SHORT), DEFAULT_CATEGORY_WEIGHTS, tradingview=-2.0,
    )  # total ~0
    # A NONE-direction coin can never be surfaced regardless of its (unscored) total.
    none_coin = model.score(
        "NONE", Direction.NONE, features, structure, context, DEFAULT_CATEGORY_WEIGHTS,
        tradingview=2.0,
    )

    surfaced = surface([strong, weak, none_coin], threshold=50.0)

    assert [s.symbol for s in surfaced] == ["STRONG"]
    assert all(s.direction is not Direction.NONE for s in surfaced)


def test_absent_tradingview_still_scores_but_lowers_trend_and_momentum() -> None:
    features, structure = _maps(bullish=True)
    context = MarketContext(btc_direction=Direction.LONG)
    model = ScoringModel()

    with_tv = model.score(
        "X", Direction.LONG, features, structure, context, DEFAULT_CATEGORY_WEIGHTS,
        tradingview=2.0,
    )
    without_tv = model.score(
        "X", Direction.LONG, features, structure, context, DEFAULT_CATEGORY_WEIGHTS,
        tradingview=None,  # simulated rate limit
    )

    trend_with = with_tv.category(CATEGORY_TREND)
    trend_without = without_tv.category(CATEGORY_TREND)
    momentum_with = with_tv.category(CATEGORY_MOMENTUM)
    momentum_without = without_tv.category(CATEGORY_MOMENTUM)
    assert trend_with is not None and trend_without is not None
    assert momentum_with is not None and momentum_without is not None

    # The coin is still scored and still surfaces at the same threshold, but the
    # TradingView-fed categories contribute less without its recommendation.
    assert without_tv.total < with_tv.total
    assert trend_without.points < trend_with.points
    assert momentum_without.points < momentum_with.points
    assert surface([without_tv], threshold=50.0)  # still clears a reasonable threshold


def test_surface_orders_by_total_descending() -> None:
    features, structure = _maps(bullish=True)
    model = ScoringModel()
    high = model.score(
        "HIGH", Direction.LONG, features, structure, MarketContext(btc_direction=Direction.LONG),
        DEFAULT_CATEGORY_WEIGHTS, tradingview=2.0,
    )
    low = model.score(
        "LOW", Direction.LONG, features, structure, MarketContext(),
        DEFAULT_CATEGORY_WEIGHTS, tradingview=None,
    )

    surfaced = surface([low, high], threshold=40.0)

    assert [s.symbol for s in surfaced] == ["HIGH", "LOW"]


def test_volume_fraction_high_confirms_and_flat_does_not() -> None:
    # High relative volume (2.0 vs 1.5 multiple) with OBV rising in the long direction.
    high = _volume_fraction(_features(bullish=True), Direction.LONG, REL_VOL_MULTIPLE)
    # Flat, average-sized volume with OBV not rising with the long.
    flat = _volume_fraction(_features(bullish=False), Direction.LONG, REL_VOL_MULTIPLE)

    assert high >= 0.9
    assert flat <= 0.1


def test_breakout_fraction_clean_break_beats_extended_move() -> None:
    state = _structure(bullish=True)  # last swing high 2.0, last swing low 1.0
    features = _features(bullish=True)

    clean = _breakout_fraction(2.0, state, features, Direction.LONG)  # close at resistance
    extended = _breakout_fraction(4.0, state, features, Direction.LONG)  # far beyond it

    assert clean > extended


def test_liquidity_fraction_deep_tight_confirms_thin_wide_does_not() -> None:
    ample = _liquidity_fraction(_order_book(liquid=True), MIN_DEPTH, MAX_SPREAD)
    poor = _liquidity_fraction(_order_book(liquid=False), MIN_DEPTH, MAX_SPREAD)

    assert ample >= 0.9
    assert poor <= 0.1


def test_market_data_categories_change_the_total() -> None:
    features, structure = _maps(bullish=True)
    context = MarketContext(btc_direction=Direction.LONG)
    model = ScoringModel()

    # Same core (trend/structure/momentum/BTC) inputs; only the market-data categories
    # differ: a confirming book + breakout + high volume versus none of that.
    with_market_data = model.score(
        "X", Direction.LONG, features, structure, context, DEFAULT_CATEGORY_WEIGHTS,
        tradingview=2.0,
        order_book=_order_book(liquid=True),
        close_by_tf={"4h": 2.0, "1d": 2.0},
        relative_volume_multiple=REL_VOL_MULTIPLE,
        min_depth=MIN_DEPTH,
        max_spread=MAX_SPREAD,
    )
    weak_features, _ = _maps(bullish=False)  # flat volume / OBV against
    without_market_data = model.score(
        "X", Direction.LONG,
        {"4h": weak_features["4h"], "1d": weak_features["1d"]}, structure, context,
        DEFAULT_CATEGORY_WEIGHTS, tradingview=2.0,
        order_book=_order_book(liquid=False),
        close_by_tf={"4h": 0.5, "1d": 0.5},  # broken below support -> no breakout credit
        relative_volume_multiple=REL_VOL_MULTIPLE,
        min_depth=MIN_DEPTH,
        max_spread=MAX_SPREAD,
    )

    assert with_market_data.total > without_market_data.total

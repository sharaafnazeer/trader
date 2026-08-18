"""The 0-100 multi-factor scoring model.

:class:`ScoringModel` turns a coin's decided :class:`~trader.direction.Direction`
together with its per-timeframe features and structure and the per-run BTC context
into a :class:`QualityScore`: a total in ``0..100`` plus a per-category breakdown.
Every category yields a *fraction* in ``[0, 1]`` (directional partial credit) that is
multiplied by its configured weight, so the score is smooth rather than
all-or-nothing.

This phase implements all eight categories for a full 100 points. Alongside the
feature/structure/BTC-driven trio (Trend alignment 25, Market structure 20, Momentum
15, BTC alignment 5) it scores the market-data-driven trio (Volume confirmation 15
from relative volume and OBV, Breakout/pullback quality 10 from price action against
the detected structure/pivots, and Liquidity 5 from the order-book snapshot's depth
and spread) and Risk-to-reward (5) from the trade plan's ratio — full credit at or
above the configured target and scaled below it. When no trade plan is available the
Risk-to-reward category degrades to zero so the coin is still scored on its other
factors.

TradingView's aggregated recommendation is folded into the Trend and Momentum
categories as a *best-effort* input: it is a numeric agreement in ``[-2, 2]`` when
available and ``None`` when the fetch failed (rate-limited). When absent it
contributes nothing, so those two categories score lower but the coin is still fully
scored from the Binance-derived factors.

This module belongs to the analysis core: it is pure and deterministic, performs no
I/O, and does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from trader.config import (
    CATEGORY_BREAKOUT,
    CATEGORY_BTC,
    CATEGORY_LIQUIDITY,
    CATEGORY_MOMENTUM,
    CATEGORY_RISK_REWARD,
    CATEGORY_STRUCTURE,
    CATEGORY_TREND,
    CATEGORY_VOLUME,
    DEFAULT_MAX_SPREAD,
    DEFAULT_MIN_DEPTH,
    DEFAULT_RELATIVE_VOLUME_MULTIPLE,
    DEFAULT_TARGET_RR,
)
from trader.direction import Direction, MarketContext
from trader.indicators import TimeframeFeatures
from trader.market_data import OrderBook
from trader.structure import Structure, StructureState

# Fraction of the Trend and Momentum categories sourced from TradingView's
# recommendation; the remainder comes from the Binance-derived indicators. Kept small
# and best-effort because TradingView rate-limits aggressively — the model must stand
# on the Binance factors alone when it is unavailable.
TV_BLEND = 0.2

# All eight scored categories; the model now reaches the full 100 points.
IMPLEMENTED_CATEGORIES: tuple[str, ...] = (
    CATEGORY_TREND,
    CATEGORY_STRUCTURE,
    CATEGORY_MOMENTUM,
    CATEGORY_VOLUME,
    CATEGORY_BREAKOUT,
    CATEGORY_BTC,
    CATEGORY_LIQUIDITY,
    CATEGORY_RISK_REWARD,
)


@dataclass(frozen=True)
class CategoryScore:
    """One category's fractional credit, its weight, and the resulting points."""

    name: str
    fraction: float
    weight: float
    points: float


@dataclass(frozen=True)
class QualityScore:
    """A coin's total score together with the per-category breakdown behind it.

    ``total`` is the sum of every category's points, reaching the full 100 for a
    fully-confirming setup now that all eight categories are implemented.
    """

    symbol: str
    direction: Direction
    total: float
    categories: tuple[CategoryScore, ...]

    def category(self, name: str) -> CategoryScore | None:
        """The scored category with ``name``, or ``None`` if it was not scored."""
        for category in self.categories:
            if category.name == name:
                return category
        return None


def _clamp01(value: float) -> float:
    """Clamp a value into ``[0, 1]``."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _mean(values: list[float]) -> float:
    """Arithmetic mean of ``values``; ``0.0`` for an empty list."""
    return sum(values) / len(values) if values else 0.0


def _tv_agreement(tv_value: float, direction: Direction) -> float:
    """Map a TradingView recommendation in ``[-2, 2]`` to a ``[0, 1]`` agreement.

    For a long, ``+2`` (strong buy) is full agreement and ``-2`` (strong sell) none;
    for a short the mapping is mirrored.
    """

    if direction is Direction.LONG:
        return _clamp01((tv_value + 2.0) / 4.0)
    return _clamp01((2.0 - tv_value) / 4.0)


def _stack_fraction(features: TimeframeFeatures, direction: Direction) -> float:
    """How well the EMA 20/50/200 stack agrees with ``direction`` (0, 0.5, or 1.0)."""

    if direction is Direction.LONG:
        pairs = [features.ema20 > features.ema50, features.ema50 > features.ema200]
    else:
        pairs = [features.ema20 < features.ema50, features.ema50 < features.ema200]
    return sum(1 for ok in pairs if ok) / len(pairs)


def _structure_fraction(state: StructureState, direction: Direction) -> float:
    """Credit for a timeframe's structure: aligned 1.0, opposed 0.0, broken 0.5."""

    aligned = Structure.BULLISH if direction is Direction.LONG else Structure.BEARISH
    opposed = Structure.BEARISH if direction is Direction.LONG else Structure.BULLISH
    if state.structure is aligned:
        return 1.0
    if state.structure is opposed:
        return 0.0
    return 0.5


def _momentum_fraction(features: TimeframeFeatures, direction: Direction) -> float:
    """Momentum credit for one timeframe from RSI, MACD, and ROC (mean of the three)."""

    if direction is Direction.LONG:
        rsi_c = _clamp01(features.rsi / 100.0)
        macd_pair = [features.macd > features.macd_signal, features.macd_hist > 0.0]
        roc_c = 1.0 if features.roc > 0.0 else 0.0
    else:
        rsi_c = _clamp01((100.0 - features.rsi) / 100.0)
        macd_pair = [features.macd < features.macd_signal, features.macd_hist < 0.0]
        roc_c = 1.0 if features.roc < 0.0 else 0.0
    macd_c = sum(1 for ok in macd_pair if ok) / len(macd_pair)
    return _mean([rsi_c, macd_c, roc_c])


def _btc_fraction(btc: MarketContext, direction: Direction) -> float:
    """Credit for BTC agreement: same direction 1.0, opposing 0.0, no regime 0.5."""

    if btc.btc_direction is Direction.NONE:
        return 0.5
    return 1.0 if btc.btc_direction is direction else 0.0


def _volume_fraction(
    features: TimeframeFeatures, direction: Direction, relative_volume_multiple: float
) -> float:
    """Volume-confirmation credit for one timeframe (mean of two components).

    The first component grows from 0 (an average-sized candle, relative volume 1.0) to
    1.0 once relative volume reaches ``relative_volume_multiple``; the second is full
    when OBV is trending *with* the trade direction (rising for a long, falling for a
    short) and zero otherwise. A flat, average-volume candle scores near zero; a
    high-volume candle whose OBV confirms the direction scores near one.
    """

    span = relative_volume_multiple - 1.0
    if span <= 0.0:
        rel_component = 1.0 if features.relative_volume >= relative_volume_multiple else 0.0
    else:
        rel_component = _clamp01((features.relative_volume - 1.0) / span)

    if direction is Direction.LONG:
        obv_component = 1.0 if features.obv_slope > 0.0 else 0.0
    else:
        obv_component = 1.0 if features.obv_slope < 0.0 else 0.0

    return _mean([rel_component, obv_component])


def _breakout_fraction(
    close: float, state: StructureState, features: TimeframeFeatures, direction: Direction
) -> float:
    """Breakout/pullback-quality credit for one timeframe from price vs structure.

    For a long, a close at or just beyond the last swing high is a clean breakout
    (full credit, decaying as the move extends past resistance by more than the swing
    range); a close inside the range that has pulled back toward support while holding
    above the fast EMA earns credit for a healthy pullback (highest nearer support),
    while losing the fast EMA or breaking support scores low. A short mirrors this
    around the last swing low. With too little structure to judge, credit is neutral.
    """

    high = state.last_swing_high
    low = state.last_swing_low
    if high is None or low is None:
        return 0.5
    span = high - low
    if span <= 0.0:
        return 0.5

    if direction is Direction.LONG:
        if close >= high:
            overshoot = (close - high) / span
            return _clamp01(1.0 - overshoot)
        if close <= low:
            return 0.0
        if close < features.ema20:
            return 0.2
        position = (close - low) / span
        return _clamp01(1.0 - position)

    if close <= low:
        overshoot = (low - close) / span
        return _clamp01(1.0 - overshoot)
    if close >= high:
        return 0.0
    if close > features.ema20:
        return 0.2
    position = (high - close) / span
    return _clamp01(1.0 - position)


def _liquidity_fraction(order_book: OrderBook, min_depth: float, max_spread: float) -> float:
    """Liquidity credit from the order book: depth and relative spread must both pass.

    Depth credit scales from 0 to 1 as the binding side's cumulative depth — measured as
    quote-currency *notional* (base-asset depth × mid price) — reaches ``min_depth``;
    spread credit falls from 1 to 0 as the relative spread ``(ask - bid) / mid``
    approaches ``max_spread``. Notional depth keeps the threshold unit-consistent across
    coins of any unit price (a flat base-asset floor wrongly penalized high-priced coins
    like BTC/ETH). The two are combined with a minimum so that failing *either* gate (thin
    book or wide spread) drags the whole category low, matching how an untradeable coin
    should score.
    """

    mid = (order_book.best_bid + order_book.best_ask) / 2.0
    if mid <= 0.0:
        return 0.0

    depth_notional = min(order_book.bid_depth, order_book.ask_depth) * mid
    depth_credit = _clamp01(depth_notional / min_depth) if min_depth > 0.0 else 1.0

    spread_ratio = order_book.spread / mid
    if max_spread > 0.0:
        spread_credit = _clamp01((max_spread - spread_ratio) / max_spread)
    else:
        spread_credit = 1.0 if spread_ratio <= 0.0 else 0.0

    return min(depth_credit, spread_credit)


def _rr_fraction(risk_reward: float | None, target_rr: float) -> float:
    """Risk-to-reward credit: full at or above ``target_rr``, scaled linearly below.

    A missing plan (``risk_reward is None``) contributes nothing, so a setup without a
    computable plan is still scored on its other factors. A non-positive target grants
    full credit rather than dividing by zero.
    """

    if risk_reward is None:
        return 0.0
    if target_rr <= 0.0:
        return 1.0
    return _clamp01(risk_reward / target_rr)


def _breakout_credit(
    direction: Direction,
    features_by_tf: dict[str, TimeframeFeatures],
    structure_by_tf: dict[str, StructureState],
    close_by_tf: dict[str, float] | None,
) -> float:
    """Mean breakout/pullback credit across the timeframes with a close available.

    Degrades to neutral (0.5) when no latest close is supplied, so the coin is still
    scored on its other factors rather than being penalized for missing input.
    """

    if close_by_tf is None:
        return 0.5
    per_tf = [
        _breakout_fraction(close_by_tf[tf], structure_by_tf[tf], features_by_tf[tf], direction)
        for tf in features_by_tf
        if tf in close_by_tf and tf in structure_by_tf
    ]
    return _mean(per_tf) if per_tf else 0.5


def _blend_tv(binance_fraction: float, tv_value: float | None, direction: Direction) -> float:
    """Blend a Binance-derived fraction with TradingView's best-effort agreement.

    When ``tv_value`` is ``None`` (fetch failed) the TradingView portion contributes
    nothing, so the category scores lower but is still driven by the Binance factors.
    """

    if tv_value is None:
        return binance_fraction * (1.0 - TV_BLEND)
    return binance_fraction * (1.0 - TV_BLEND) + _tv_agreement(tv_value, direction) * TV_BLEND


class ScoringModel:
    """Scores a coin 0-100 across the weighted categories. Pure and deterministic."""

    def score(
        self,
        symbol: str,
        direction: Direction,
        features_by_tf: dict[str, TimeframeFeatures],
        structure_by_tf: dict[str, StructureState],
        btc_context: MarketContext,
        weights: dict[str, float],
        tradingview: float | None = None,
        order_book: OrderBook | None = None,
        close_by_tf: dict[str, float] | None = None,
        relative_volume_multiple: float = DEFAULT_RELATIVE_VOLUME_MULTIPLE,
        min_depth: float = DEFAULT_MIN_DEPTH,
        max_spread: float = DEFAULT_MAX_SPREAD,
        risk_reward: float | None = None,
        target_rr: float = DEFAULT_TARGET_RR,
    ) -> QualityScore:
        """Score ``symbol`` for its decided ``direction``.

        ``direction`` must be ``LONG`` or ``SHORT`` (a ``NONE`` coin is never scored).
        ``tradingview`` is the aggregated recommendation in ``[-2, 2]`` or ``None``
        when unavailable. ``order_book`` and ``close_by_tf`` (the latest close per
        timeframe) feed the market-data categories; when either is absent that
        category degrades — Liquidity to zero, Breakout to neutral — so the coin is
        still scored on the remaining factors. ``risk_reward`` is the trade plan's
        ratio feeding the Risk-to-reward category (full credit at or above
        ``target_rr``, scaled below); ``None`` (no plan) degrades it to zero. Each
        implemented category contributes ``fraction × weight``; an unweighted category
        contributes nothing.
        """

        trend_binance = _mean(
            [_stack_fraction(f, direction) for f in features_by_tf.values()]
        )
        trend_fraction = _blend_tv(trend_binance, tradingview, direction)

        structure_fraction = _mean(
            [_structure_fraction(s, direction) for s in structure_by_tf.values()]
        )

        momentum_binance = _mean(
            [_momentum_fraction(f, direction) for f in features_by_tf.values()]
        )
        momentum_fraction = _blend_tv(momentum_binance, tradingview, direction)

        volume_fraction = _mean(
            [
                _volume_fraction(f, direction, relative_volume_multiple)
                for f in features_by_tf.values()
            ]
        )

        breakout_fraction = _breakout_credit(
            direction, features_by_tf, structure_by_tf, close_by_tf
        )

        btc_fraction = _btc_fraction(btc_context, direction)

        liquidity_fraction = (
            _liquidity_fraction(order_book, min_depth, max_spread)
            if order_book is not None
            else 0.0
        )

        rr_fraction = _rr_fraction(risk_reward, target_rr)

        fractions = {
            CATEGORY_TREND: trend_fraction,
            CATEGORY_STRUCTURE: structure_fraction,
            CATEGORY_MOMENTUM: momentum_fraction,
            CATEGORY_VOLUME: volume_fraction,
            CATEGORY_BREAKOUT: breakout_fraction,
            CATEGORY_BTC: btc_fraction,
            CATEGORY_LIQUIDITY: liquidity_fraction,
            CATEGORY_RISK_REWARD: rr_fraction,
        }

        categories: list[CategoryScore] = []
        for name in IMPLEMENTED_CATEGORIES:
            fraction = _clamp01(fractions[name])
            weight = weights.get(name, 0.0)
            categories.append(
                CategoryScore(
                    name=name,
                    fraction=fraction,
                    weight=weight,
                    points=fraction * weight,
                )
            )

        total = sum(category.points for category in categories)
        return QualityScore(
            symbol=symbol,
            direction=direction,
            total=total,
            categories=tuple(categories),
        )


def surface(scores: Iterable[QualityScore], threshold: float) -> tuple[QualityScore, ...]:
    """The ranked short list: coins with a direction and a total ``>= threshold``.

    Coins with direction ``NONE`` or a total below ``threshold`` are excluded; the
    survivors are ordered by total descending (highest conviction first).
    """

    eligible = [
        score
        for score in scores
        if score.direction is not Direction.NONE and score.total >= threshold
    ]
    return tuple(sorted(eligible, key=lambda score: score.total, reverse=True))

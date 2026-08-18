"""The 0-100 momentum / breakout scoring model (the "movers" engine).

This is a **second, independent** scanner alongside the trend-following scoring model
in :mod:`trader.scoring_model`. Where the trend engine rewards coins already in a
clean, proven trend, this one rewards coins that are *actually moving* — outperforming
the market, breaking out to new recent highs on real volume, and accelerating.

:class:`MomentumModel.score` turns a coin's live daily candles and the per-run BTC
benchmark return into a :class:`MomentumScore`: a total in ``0..100`` plus a
per-factor breakdown and a direction (a strong up-move is a LONG candidate, a strong
down-move a SHORT candidate). It mirrors :mod:`trader.scoring_model`'s
fractional-credit × weight pattern over four factors — relative strength vs BTC,
breakout, volume expansion, and acceleration — each yielding a fraction in ``[0, 1]``
multiplied by its configured weight, so the score is smooth rather than all-or-nothing.

This module belongs to the analysis core: it is pure and deterministic, performs no
I/O, and does not import ``typer`` or ``rich``. It reuses
:func:`trader.indicators.compute_features` for relative volume / ROC / ATR.
"""

from __future__ import annotations

from dataclasses import dataclass

from trader.config import (
    DEFAULT_BREAKOUT_LOOKBACK_DAYS,
    DEFAULT_MOMENTUM_WEIGHTS,
    DEFAULT_RS_LOOKBACK_DAYS,
    MOMENTUM_FACTOR_ACCELERATION,
    MOMENTUM_FACTOR_BREAKOUT,
    MOMENTUM_FACTOR_RELATIVE_STRENGTH,
    MOMENTUM_FACTOR_VOLUME,
    MOMENTUM_FACTORS,
)
from trader.direction import Direction
from trader.indicators import compute_features
from trader.market_data import Candles, OrderBook

# Normalization scales that turn a raw factor value into a fraction in ``[0, 1]``.
# In-code defaults for this phase, mirroring how the trend scoring model keeps its
# tunables in code alongside the weights.
# Relative strength (coin return minus BTC return, in the trade direction) reaches full
# factor credit at this much out/under-performance over the lookback (20%).
RS_FULL_SCALE = 0.20
# Relative volume (latest candle vs its rolling average) reaches full volume credit at
# this multiple; 1.0 is an average-sized candle so credit grows from there.
VOLUME_FULL_MULTIPLE = 2.0
# ROC (percent) in the trade direction reaches full acceleration credit at this value.
ACCELERATION_FULL_ROC = 10.0


def _clamp01(value: float) -> float:
    """Clamp a value into ``[0, 1]``."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


@dataclass(frozen=True)
class MomentumFactorScore:
    """One momentum factor's fractional credit, its weight, and resulting points."""

    name: str
    fraction: float
    weight: float
    points: float


@dataclass(frozen=True)
class MomentumScore:
    """A coin's momentum total together with the per-factor breakdown behind it.

    ``score`` is the sum of every factor's points (``0..100`` when the weights sum to
    100). ``direction`` is ``LONG`` for an up-move and ``SHORT`` for a down-move — the
    sign of the coin's dominant momentum. ``factors`` is the per-factor breakdown in a
    stable order.
    """

    symbol: str
    score: float
    direction: Direction
    factors: tuple[MomentumFactorScore, ...]

    def factor(self, name: str) -> MomentumFactorScore | None:
        """The scored factor with ``name``, or ``None`` if it was not scored."""
        for factor in self.factors:
            if factor.name == name:
                return factor
        return None


def relative_strength(coin_return: float, btc_return: float) -> float:
    """Relative strength: the coin's return minus BTC's return over the lookback.

    Positive means the coin is outperforming the market benchmark; negative means it is
    lagging it. Pure and direction-agnostic — the model applies the trade direction
    (a down-mover leads to the downside when it *under*performs) before scoring.
    """

    return coin_return - btc_return


def breakout_fraction(
    close: float, trailing_high_or_low: float, atr: float, direction: Direction
) -> float:
    """ATR-normalized breakout credit: how far ``close`` has pushed past the level.

    For a LONG this is how far the close is *above* the trailing N-day high; for a
    SHORT it is how far it is *below* the trailing N-day low. The distance is
    normalized by ATR so it is comparable across coins, reaching full credit (1.0) once
    the close is a full ATR beyond the level and zero when it has not cleared the level
    at all. A non-positive ATR yields no credit rather than dividing by zero.
    """

    if atr <= 0.0:
        return 0.0
    if direction is Direction.SHORT:
        distance = (trailing_high_or_low - close) / atr
    else:
        distance = (close - trailing_high_or_low) / atr
    return _clamp01(distance)


def period_return(candles: Candles, lookback: int) -> float:
    """The simple return of ``close`` over the last ``lookback`` candles.

    ``(latest_close / close_lookback_ago) - 1``. Falls back to the earliest available
    close when the frame is shorter than ``lookback`` candles, and yields ``0.0`` when
    the reference close is non-positive, so a short or degenerate frame never raises.
    """

    closes = candles.frame["close"]
    n = len(closes)
    if n < 2:
        return 0.0
    index = max(0, n - 1 - lookback)
    reference = float(closes.iloc[index])
    if reference <= 0.0:
        return 0.0
    return float(closes.iloc[-1]) / reference - 1.0


def _trailing_level(candles: Candles, lookback: int, direction: Direction) -> float:
    """The trailing breakout level: the prior ``lookback`` candles' high (LONG) or low.

    The current (latest) candle is excluded so a breakout is measured against the level
    it is breaking. Falls back to the whole frame when it is shorter than the window.
    """

    frame = candles.frame
    n = len(frame)
    if n < 2:
        column = "high" if direction is not Direction.SHORT else "low"
        return float(frame[column].iloc[-1])
    start = max(0, n - 1 - lookback)
    window = frame.iloc[start : n - 1]
    if direction is Direction.SHORT:
        return float(window["low"].min())
    return float(window["high"].max())


def trailing_breakout_level(candles: Candles, lookback: int, direction: Direction) -> float:
    """The trailing breakout level: the prior ``lookback`` candles' high (LONG) / low (SHORT).

    A thin public wrapper over the level the scoring model measures a breakout against,
    exposed so the momentum trade plan can anchor its stop to the same level (the stop
    sits an ATR buffer beyond it). The current candle is excluded so the level is the one
    the latest move is breaking.
    """

    return _trailing_level(candles, lookback, direction)


def _directional(value: float, direction: Direction) -> float:
    """The value as seen in the trade direction: unchanged for LONG, negated for SHORT.

    A down-mover's momentum is strong when its return / acceleration are strongly
    negative, so negating them turns "falling hard" into positive SHORT credit.
    """

    return value if direction is not Direction.SHORT else -value


class MomentumModel:
    """Scores a coin 0-100 on momentum across four weighted factors. Pure."""

    def score(
        self,
        symbol: str,
        candles: Candles,
        btc_return: float,
        weights: dict[str, float] | None = None,
        *,
        rs_lookback: int = DEFAULT_RS_LOOKBACK_DAYS,
        breakout_lookback: int = DEFAULT_BREAKOUT_LOOKBACK_DAYS,
    ) -> MomentumScore:
        """Score ``symbol`` from its daily ``candles`` and the BTC benchmark return.

        The coin's own return over ``rs_lookback`` decides the direction (up-momentum
        → LONG, down-momentum → SHORT); its magnitude and the other factors decide the
        score. Each factor yields a fraction in ``[0, 1]`` multiplied by its weight:

        - **relative strength** — the coin's return minus BTC's, in the trade
          direction, normalized by :data:`RS_FULL_SCALE`;
        - **breakout** — ATR-normalized distance past the trailing high (LONG) / low
          (SHORT) over ``breakout_lookback`` (see :func:`breakout_fraction`);
        - **volume** — relative volume (latest candle vs its rolling average) scaled to
          full credit at :data:`VOLUME_FULL_MULTIPLE`;
        - **acceleration** — ROC in the trade direction, scaled to full credit at
          :data:`ACCELERATION_FULL_ROC`.

        ``weights`` defaults to :data:`~trader.config.DEFAULT_MOMENTUM_WEIGHTS`; an
        unweighted factor contributes nothing.
        """

        factor_weights = weights if weights is not None else dict(DEFAULT_MOMENTUM_WEIGHTS)

        features = compute_features(candles)
        close = candles.latest_close
        coin_return = period_return(candles, rs_lookback)

        # Direction is the sign of the dominant momentum (the coin's own return over the
        # relative-strength window): up-momentum is a long candidate, down a short.
        direction = Direction.SHORT if coin_return < 0.0 else Direction.LONG

        rs = relative_strength(coin_return, btc_return)
        rs_fraction = _clamp01(_directional(rs, direction) / RS_FULL_SCALE)

        level = _trailing_level(candles, breakout_lookback, direction)
        breakout = breakout_fraction(close, level, features.atr, direction)

        span = VOLUME_FULL_MULTIPLE - 1.0
        volume_fraction = (
            _clamp01((features.relative_volume - 1.0) / span) if span > 0.0 else 0.0
        )

        acceleration_fraction = _clamp01(
            _directional(features.roc, direction) / ACCELERATION_FULL_ROC
        )

        fractions = {
            MOMENTUM_FACTOR_RELATIVE_STRENGTH: rs_fraction,
            MOMENTUM_FACTOR_BREAKOUT: breakout,
            MOMENTUM_FACTOR_VOLUME: volume_fraction,
            MOMENTUM_FACTOR_ACCELERATION: acceleration_fraction,
        }

        factors: list[MomentumFactorScore] = []
        for name in MOMENTUM_FACTORS:
            fraction = _clamp01(fractions[name])
            weight = factor_weights.get(name, 0.0)
            factors.append(
                MomentumFactorScore(
                    name=name,
                    fraction=fraction,
                    weight=weight,
                    points=fraction * weight,
                )
            )

        total = sum(factor.points for factor in factors)
        return MomentumScore(
            symbol=symbol,
            score=total,
            direction=direction,
            factors=tuple(factors),
        )


def passes_liquidity(order_book: OrderBook, min_depth: float, max_spread: float) -> bool:
    """Whether a coin's order book is liquid enough to trade the mover.

    A coin passes when the binding side's cumulative depth — measured as quote-currency
    *notional* (base-asset depth × mid price) — is at least ``min_depth`` and the relative
    spread ``(ask - bid) / mid`` is at most ``max_spread``. Notional depth keeps the
    threshold unit-consistent across coins of any unit price (a flat base-asset floor
    wrongly excluded high-priced coins like BTC/ETH). A coin failing either gate is
    excluded from the surfaced movers (filtered out, not scored to zero), matching how an
    untradeable pump should be handled.
    """

    mid = (order_book.best_bid + order_book.best_ask) / 2.0
    if mid <= 0.0:
        return False
    depth_notional = min(order_book.bid_depth, order_book.ask_depth) * mid
    if depth_notional < min_depth:
        return False
    return (order_book.spread / mid) <= max_spread

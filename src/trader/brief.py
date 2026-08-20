"""The evidence pack handed to the AI analyst.

A :class:`SetupBrief` is the *only* thing the analyst ever sees about a coin. It is a
deliberately narrow, flat projection of what the engine already computed — the decided
direction, the score and its category breakdown, a per-timeframe indicator summary, the
mechanical trade plan, the order-book liquidity, and the limited-history marker — plus a
once-per-run :class:`MarketBrief` carrying BTC's regime.

Two properties are load-bearing:

* **Bounded.** Only the latest value of each indicator is carried, never a series and
  never a :class:`~trader.market_data.Candles` frame. The payload for a coin is a few
  hundred tokens regardless of how many candles were fetched.
* **JSON-ready.** :func:`brief_to_dict` emits plain scalars only, with non-finite floats
  (which ``ta`` produces on short history) normalized to ``None`` so the result is valid
  JSON under a strict encoder rather than the ``NaN`` extension.

:func:`render_briefs` turns the briefs into the compact text block a model would receive.
Keeping rendering pure and separate from any API client is what lets a test assert what
the model would be told without a network.

This module belongs to the analysis core: it is pure and deterministic, performs no I/O,
and does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from trader.direction import Direction
from trader.indicators import TimeframeFeatures, compute_features
from trader.market_data import Candles, OrderBook
from trader.momentum import MomentumScore
from trader.strategy import SetupVerdict
from trader.structure import StructureState
from trader.trade_planner import TradePlan

# Reported instead of a structure classification when the scanner never measured one.
STRUCTURE_NOT_MEASURED = "not-measured"

# How the EMA 20/50/200 stack is described to the analyst. The stack *drives* the
# engine's direction decision (see :mod:`trader.direction`), so its ordering is the single
# most informative thing about a timeframe and is stated explicitly rather than left to be
# re-derived from three raw numbers.
EMA_STACK_BULLISH = "10>21>50>200"
EMA_STACK_BEARISH = "10<21<50<200"
EMA_STACK_MIXED = "mixed"


def _finite(value: float | None) -> float | None:
    """Return ``value`` when it is a finite number, else ``None``.

    ``ta`` yields ``NaN`` for windows longer than the available history. ``NaN`` is not
    representable in strict JSON and means nothing to a reader, so it is normalized to an
    explicit absence.
    """

    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _ema_stack(features: TimeframeFeatures) -> str:
    """Describe the EMA stack ordering in the same terms the direction rule uses."""

    if features.ema10 > features.ema21 > features.ema50 > features.sma200:
        return EMA_STACK_BULLISH
    if features.ema10 < features.ema21 < features.ema50 < features.sma200:
        return EMA_STACK_BEARISH
    return EMA_STACK_MIXED


@dataclass(frozen=True)
class TimeframeBrief:
    """One timeframe's evidence: the trend shape, the oscillators, and the structure.

    ``atr_pct`` is ATR as a percentage of the latest close, which is comparable across
    coins in a way an absolute ATR is not. ``relative_volume`` is the latest candle's
    volume over its rolling average (``1.0`` is average-sized).
    """

    timeframe: str
    close: float | None
    ema_stack: str
    atr_pct: float | None
    relative_volume: float | None
    structure: str
    last_swing_high: float | None
    last_swing_low: float | None
    ema10: float | None = None
    ema21: float | None = None
    ema50: float | None = None
    sma200: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    stoch_rsi_k: float | None = None
    stoch_rsi_d: float | None = None
    stoch_cross: str | None = None
    macd_cross: str | None = None
    reaction_patterns: tuple[str, ...] = ()
    trendline: str | None = None


@dataclass(frozen=True)
class CategoryBrief:
    """One scored category's contribution to the engine total."""

    name: str
    points: float
    weight: float


@dataclass(frozen=True)
class PlanBrief:
    """The engine's mechanical trade plan for the setup.

    """

    entry: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    invalidation: float


@dataclass(frozen=True)
class LiquidityBrief:
    """Order-book liquidity at the moment of the scan.

    ``spread_pct`` is the relative spread over the mid price and the depths are quote
    (USDT) *notional* across the sampled levels, matching the units the liquidity floor is
    configured in.
    """

    spread_pct: float | None
    bid_depth_notional: float | None
    ask_depth_notional: float | None


@dataclass(frozen=True)
class MarketBrief:
    """Per-run market context.

    ``btc_direction`` is BTC's own regime as the trend engine decided it.
    ``btc_return_pct`` is BTC's return over the momentum scanner's lookback — the
    benchmark every mover's relative strength was measured against, and meaningless
    without it.
    """

    btc_direction: Direction = Direction.NONE
    btc_return_pct: float | None = None


@dataclass(frozen=True)
class VerdictBrief:
    """The engine's four-part read on the setup, as the analyst receives it.

    Carried so the model reasons from the same checklist the engine did rather than
    inventing its own framework. ``conditions`` is every row with its measurement, so where
    the engine says WAIT the model can see precisely which condition failed and by how much
    — and disagree with it on the evidence rather than on a summary.
    """

    trend: str
    strategy: str | None
    entry_status: str
    decision: str
    reason: str
    conditions_met: int
    conditions_evaluated: int
    conditions: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class SetupBrief:
    """Everything the analyst is told about one candidate setup.

    The same shape serves both scanners. ``total`` is the trend engine's 0-100 quality
    score or the momentum scanner's 0-100 composite, and ``categories`` is whichever
    breakdown produced it — the two are structurally identical (name, weight, points), so
    one brief type carries both rather than forking the whole downstream path.
    ``breakout_level`` is momentum-only: the trailing high/low the move is breaking and
    the level the stop is anchored to.

    ``total`` and ``categories`` are momentum-only since 2026-08-20: the trend engine's
    0-100 score was retired with the indicators behind it, so the trend path supplies
    ``None`` and an empty breakdown, and the analyst reads the per-timeframe values instead
    of a summary number.
    """

    symbol: str
    direction: Direction
    total: float | None
    categories: tuple[CategoryBrief, ...]
    timeframes: tuple[TimeframeBrief, ...]
    plan: PlanBrief | None
    liquidity: LiquidityBrief
    limited_history: bool
    breakout_level: float | None = None
    # Recent OHLC of the decision timeframe, oldest first, so the model can read price
    # action the pattern predicates do not cover. Bounded by configuration because this is
    # the largest single contributor to prompt size.
    candles: tuple[tuple[float, float, float, float], ...] = ()
    verdict: VerdictBrief | None = None
    # Every setup that matched, deciding one included. A coin can present a trend pullback
    # and a breakout at once, and the model is told about both — two independent methods
    # pointing at one coin is different evidence from one, and only the model can weigh that.
    verdicts: tuple[VerdictBrief, ...] = ()


def _plan_brief(plan: TradePlan | None) -> PlanBrief | None:
    """Project a trade plan into its brief."""

    if plan is None:
        return None
    return PlanBrief(
        entry=plan.entry,
        stop_loss=plan.stop_loss,
        take_profit=plan.take_profit,
        risk_reward=plan.risk_reward,
        invalidation=plan.invalidation,
    )


def _recent_ohlc(
    candles: Candles | None, count: int
) -> tuple[tuple[float, float, float, float], ...]:
    """The last ``count`` bars as plain OHLC tuples, oldest first.

    Non-finite rows are dropped rather than serialized: the evidence must encode under a
    strict encoder, and a ``NaN`` in a candle would fail the whole payload for one bad bar.
    """

    if candles is None or count <= 0:
        return ()
    frame = candles.frame.tail(count)
    rows = zip(
        frame["open"].tolist(),
        frame["high"].tolist(),
        frame["low"].tolist(),
        frame["close"].tolist(),
        strict=True,
    )
    return tuple(
        (float(o), float(h), float(low), float(c))
        for o, h, low, c in rows
        if all(math.isfinite(float(v)) for v in (o, h, low, c))
    )


def _verdict_json(verdict: VerdictBrief) -> dict[str, object]:
    """One checklist read as JSON, conditions and all."""

    return {
        "trend": verdict.trend,
        "strategy": verdict.strategy,
        "entry_status": verdict.entry_status,
        "decision": verdict.decision,
        "reason": verdict.reason,
        "conditions_met": verdict.conditions_met,
        "conditions_evaluated": verdict.conditions_evaluated,
        "conditions": [
            {"name": n, "met": m, "detail": d} for n, m, d in verdict.conditions
        ],
    }


def _verdict_brief(verdict: SetupVerdict | None) -> VerdictBrief | None:
    """Project the checklist verdict into its brief, or ``None`` when the catalogue is off."""

    if verdict is None:
        return None
    return VerdictBrief(
        trend=verdict.trend.value,
        strategy=verdict.strategy.value if verdict.strategy is not None else None,
        entry_status=verdict.entry_status.value,
        decision=verdict.decision.value,
        reason=verdict.reason,
        conditions_met=verdict.conditions_met,
        conditions_evaluated=verdict.conditions_evaluated,
        conditions=tuple(
            (c.name, "yes" if c.met else ("unknown" if c.met is None else "no"), c.detail)
            for c in verdict.conditions
        ),
    )


def _liquidity_brief(order_book: OrderBook) -> LiquidityBrief:
    """Project an order-book snapshot into relative spread and notional depth."""

    mid = (order_book.best_bid + order_book.best_ask) / 2.0
    if mid <= 0:
        return LiquidityBrief(
            spread_pct=None, bid_depth_notional=None, ask_depth_notional=None
        )
    return LiquidityBrief(
        spread_pct=_finite(order_book.spread / mid * 100.0),
        bid_depth_notional=_finite(order_book.bid_depth * mid),
        ask_depth_notional=_finite(order_book.ask_depth * mid),
    )


def _stoch_cross(features: TimeframeFeatures) -> str | None:
    """The Stochastic RSI turn as one readable phrase, or ``None`` when nothing crossed.

    The method reads this as an *event from an extreme*, so both halves are stated: which
    way it crossed, how long ago, and the level it came from. A model given only %K and %D
    would have to infer the turn from two numbers and could not see the extreme at all.
    """

    bars_ago = features.stoch_cross_bars_ago
    if bars_ago is None:
        return None
    way = "up" if features.stoch_crossed_up else "down"
    extreme = features.stoch_cross_extreme
    from_part = f" from {extreme:.2f}" if math.isfinite(extreme) else ""
    return f"{way} {bars_ago} bar(s) ago{from_part}"


def _macd_cross(features: TimeframeFeatures) -> str | None:
    """The MACD turn and its side of zero, or ``None`` when nothing crossed recently.

    The zero-line side is stated even without a cross, because the method reads the two
    together: a crossover above zero and one deep in negative territory are different
    evidence wearing the same name.
    """

    bars_ago = features.macd_cross_bars_ago
    side = "above zero" if features.macd > 0.0 else "below zero"
    if bars_ago is None:
        return side
    way = "up" if features.macd_crossed_up else "down"
    return f"{way} {bars_ago} bar(s) ago, {side}"


def _timeframe_brief(
    timeframe: str,
    features: TimeframeFeatures,
    structure: StructureState | None,
    close: float | None,
    patterns: tuple[str, ...] = (),
    trendline: str | None = None,
) -> TimeframeBrief:
    """Project one timeframe's features and structure into its brief.

    ``structure`` is ``None`` when the scanner does not detect swing structure at all
    (the momentum path anchors to a breakout level instead). That is reported as
    :data:`STRUCTURE_NOT_MEASURED` rather than as a classification, because telling a
    model "BROKEN" when nothing was measured invites it to treat an absence as a finding.
    """

    atr = _finite(features.atr)
    atr_pct = atr / close * 100.0 if atr is not None and close else None
    return TimeframeBrief(
        timeframe=timeframe,
        close=_finite(close),
        ema_stack=_ema_stack(features),
        atr_pct=_finite(atr_pct),
        relative_volume=_finite(features.relative_volume),
        structure=str(structure.structure) if structure is not None else STRUCTURE_NOT_MEASURED,
        last_swing_high=_finite(structure.last_swing_high) if structure is not None else None,
        last_swing_low=_finite(structure.last_swing_low) if structure is not None else None,
        ema10=_finite(features.ema10),
        ema21=_finite(features.ema21),
        ema50=_finite(features.ema50),
        sma200=_finite(features.sma200),
        macd=_finite(features.macd),
        macd_signal=_finite(features.macd_signal),
        stoch_rsi_k=_finite(features.stoch_rsi_k),
        stoch_rsi_d=_finite(features.stoch_rsi_d),
        stoch_cross=_stoch_cross(features),
        macd_cross=_macd_cross(features),
        reaction_patterns=patterns,
        trendline=trendline,
    )


def build_brief(
    symbol: str,
    direction: Direction,
    *,
    features_by_tf: dict[str, TimeframeFeatures],
    structure_by_tf: dict[str, StructureState],
    close_by_tf: dict[str, float],
    order_book: OrderBook,
    plan: TradePlan | None,
    limited_history: bool,
    timeframes: Sequence[str],
    verdict: SetupVerdict | None = None,
    verdicts: Sequence[SetupVerdict] = (),
    patterns_by_tf: dict[str, tuple[str, ...]] | None = None,
    trendline_by_tf: dict[str, str] | None = None,
    recent_candles: Candles | None = None,
    candle_count: int = 0,
) -> SetupBrief:
    """Build the evidence pack for one candidate.

    ``symbol`` and ``direction`` identify the candidate; the remaining arguments are the
    per-coin analysis the same run produced. ``timeframes`` fixes the ordering so the
    rendered evidence reads fastest-to-slowest exactly as the configured analysis set does;
    a timeframe absent from the analysis is skipped rather than rendered as a row of blanks.

    There is no total or category breakdown: the trend engine's 0-100 score was retired with
    the indicators behind it, so the analyst reads the per-timeframe values directly.
    """

    timeframe_briefs = tuple(
        _timeframe_brief(
            timeframe,
            features_by_tf[timeframe],
            structure_by_tf[timeframe],
            close_by_tf.get(timeframe),
            patterns=(patterns_by_tf or {}).get(timeframe, ()),
            trendline=(trendline_by_tf or {}).get(timeframe),
        )
        for timeframe in timeframes
        if timeframe in features_by_tf and timeframe in structure_by_tf
    )

    plan_brief = _plan_brief(plan)

    return SetupBrief(
        symbol=symbol,
        direction=direction,
        total=None,
        categories=(),
        timeframes=timeframe_briefs,
        plan=plan_brief,
        liquidity=_liquidity_brief(order_book),
        limited_history=limited_history,
        verdict=_verdict_brief(verdict),
        verdicts=tuple(
            b for b in (_verdict_brief(v) for v in verdicts) if b is not None
        ),
        candles=_recent_ohlc(recent_candles, candle_count),
    )


def build_mover_brief(
    score: MomentumScore,
    *,
    candles: Candles,
    order_book: OrderBook,
    plan: TradePlan | None,
    breakout_level: float | None = None,
) -> SetupBrief:
    """Build the evidence pack for one surfaced momentum mover.

    The momentum scanner measures something different from the trend engine — relative
    strength against BTC, breakout position, volume expansion, acceleration — so the
    breakdown carried here is those four factors, not the trend engine's eight categories.
    It fetches a single (daily) timeframe, so there is exactly one timeframe row.

    ``limited_history`` follows the same rule as the trend path: a coin too young for the
    full EMA stack is marked so the analyst discounts its confidence.
    """

    features = compute_features(candles)
    close = candles.latest_close
    # No structure argument: the momentum scanner detects no swing structure, and its stop
    # is anchored to the trailing breakout level below instead.
    timeframe_brief = _timeframe_brief(candles.timeframe, features, None, close)

    return SetupBrief(
        symbol=score.symbol,
        direction=score.direction,
        total=score.score,
        categories=tuple(
            CategoryBrief(name=f.name, points=f.points, weight=f.weight)
            for f in score.factors
        ),
        timeframes=(timeframe_brief,),
        plan=_plan_brief(plan),
        liquidity=_liquidity_brief(order_book),
        limited_history=features.limited_history,
        breakout_level=_finite(breakout_level),
    )


def brief_to_dict(brief: SetupBrief) -> dict[str, object]:
    """Serialize a brief into a JSON-ready dict of plain scalars.

    Every float is finite or ``None``, so the result encodes under a strict JSON encoder
    (``allow_nan=False``) rather than emitting the non-standard ``NaN`` token.
    """

    return {
        "symbol": brief.symbol,
        "direction": brief.direction.value,
        "total": brief.total,
        "limited_history": brief.limited_history,
        "categories": [
            {"name": c.name, "points": c.points, "weight": c.weight}
            for c in brief.categories
        ],
        "timeframes": [
            {
                "timeframe": t.timeframe,
                "close": t.close,
                "ema_stack": t.ema_stack,
                "atr_pct": t.atr_pct,
                "relative_volume": t.relative_volume,
                "structure": t.structure,
                "last_swing_high": t.last_swing_high,
                "last_swing_low": t.last_swing_low,
                "ema10": t.ema10,
                "ema21": t.ema21,
                "ema50": t.ema50,
                "sma200": t.sma200,
                "macd": t.macd,
                "macd_signal": t.macd_signal,
                "stoch_rsi_k": t.stoch_rsi_k,
                "stoch_rsi_d": t.stoch_rsi_d,
                "stoch_cross": t.stoch_cross,
                "macd_cross": t.macd_cross,
                "reaction_patterns": list(t.reaction_patterns),
                "trendline": t.trendline,
            }
            for t in brief.timeframes
        ],
        "plan": (
            {
                "entry": brief.plan.entry,
                "stop_loss": brief.plan.stop_loss,
                "take_profit": brief.plan.take_profit,
                "risk_reward": brief.plan.risk_reward,
                "invalidation": brief.plan.invalidation,
            }
            if brief.plan is not None
            else None
        ),
        "liquidity": {
            "spread_pct": brief.liquidity.spread_pct,
            "bid_depth_notional": brief.liquidity.bid_depth_notional,
            "ask_depth_notional": brief.liquidity.ask_depth_notional,
        },
        "breakout_level": brief.breakout_level,
        **(
            {"candles": [list(bar) for bar in brief.candles]} if brief.candles else {}
        ),
        **({"verdict": _verdict_json(brief.verdict)} if brief.verdict is not None else {}),
        # Only when a second setup matched: one verdict repeated as a one-element list
        # would cost prompt tokens to say nothing.
        **(
            {"verdicts": [_verdict_json(v) for v in brief.verdicts]}
            if len(brief.verdicts) > 1
            else {}
        ),
    }


def _number(value: float | None, spec: str = ".4g") -> str:
    """Format an optional number for the rendered evidence."""
    return format(value, spec) if value is not None else "n/a"


def render_briefs(
    briefs: Sequence[SetupBrief],
    market: MarketBrief,
    *,
    scanner: str = "scan",
    breakdown_label: str = "categories",
) -> str:
    """Render the evidence pack as the compact text block a model would receive.

    The BTC context is stated once for the whole run rather than repeated per coin, and
    each candidate is a short block: its engine verdict and score, the score breakdown,
    one line per timeframe, the trade plan, the liquidity, and — only when they apply —
    the breakout level and the limited-history caveat.

    ``scanner`` and ``breakdown_label`` let the same renderer serve both engines: the
    momentum scanner's number means something different from the trend engine's, and
    labelling it "categories" would invite the model to read it as the same thing.
    """

    lines: list[str] = []
    lines.append("MARKET CONTEXT")
    lines.append(f"  scanner: {scanner}")
    lines.append(f"  BTC regime: {market.btc_direction.value}")
    if market.btc_return_pct is not None:
        lines.append(f"  BTC benchmark return: {market.btc_return_pct:+.2f}%")
    lines.append("")
    lines.append(f"CANDIDATES ({len(briefs)})")

    if not briefs:
        lines.append("  (none selected for review)")
        return "\n".join(lines)

    for position, brief in enumerate(briefs, start=1):
        lines.append("")
        # The trend engine has no total since its score was retired; the momentum scanner
        # still has one. Omitting the clause is better than printing "score None/100".
        headline = f"[{position}] {brief.symbol} — engine {brief.direction.value}"
        if brief.total is not None:
            headline += f", score {brief.total:.1f}/100"
        lines.append(headline)
        if brief.categories:
            breakdown = "  ".join(f"{c.name}={c.points:.1f}" for c in brief.categories)
            lines.append(f"  {breakdown_label}: {breakdown}")
        if brief.breakout_level is not None:
            lines.append(f"  breakout level: {brief.breakout_level:.6g}")
        for timeframe in brief.timeframes:
            lines.append(
                f"  {timeframe.timeframe:<4} "
                f"close={_number(timeframe.close)}  "
                f"ema={timeframe.ema_stack}  "

                f"atr={_number(timeframe.atr_pct, '.2f')}%  "
                f"rvol={_number(timeframe.relative_volume, '.2f')}  "
                f"structure={timeframe.structure}  "
                f"swing_high={_number(timeframe.last_swing_high)}  "
                f"swing_low={_number(timeframe.last_swing_low)}"
                + f"  ema={_number(timeframe.ema10, '.6g')}"
                + f"/{_number(timeframe.ema21, '.6g')}"
                + f"/{_number(timeframe.ema50, '.6g')}"
                + f"/{_number(timeframe.sma200, '.6g')}"
                + f"  macd={_number(timeframe.macd, '.4g')}"
                + f"/{_number(timeframe.macd_signal, '.4g')}"
                + f"  stoch={_number(timeframe.stoch_rsi_k, '.2f')}"
                + f"/{_number(timeframe.stoch_rsi_d, '.2f')}"
                + (f"  turn={timeframe.stoch_cross}" if timeframe.stoch_cross else "")
                + (f"  macd_turn={timeframe.macd_cross}" if timeframe.macd_cross else "")
                + (
                    f"  reaction={','.join(timeframe.reaction_patterns)}"
                    if timeframe.reaction_patterns
                    else ""
                )
                + (f"  trendline={timeframe.trendline}" if timeframe.trendline else "")
            )
        if brief.plan is not None:
            lines.append(
                f"  plan: entry={brief.plan.entry:.6g}  "
                f"stop={brief.plan.stop_loss:.6g}  "
                f"target={brief.plan.take_profit:.6g}  "
                f"rr={brief.plan.risk_reward:.2f}  "
                f"invalidation={brief.plan.invalidation:.6g}"
            )
        else:
            lines.append("  plan: none (no usable invalidation pivot)")
        if brief.candles:
            lines.append("  recent candles (open, high, low, close), oldest first:")
            for bar in brief.candles:
                lines.append("    " + "  ".join(f"{value:.6g}" for value in bar))
        shown = brief.verdicts or ((brief.verdict,) if brief.verdict is not None else ())
        for v in shown:
            if v is None:
                continue
            # Which one decided the row matters when two setups matched and disagreed: the
            # model must not have to guess which read the engine acted on.
            deciding = " (deciding)" if len(shown) > 1 and v == brief.verdict else ""
            lines.append(
                f"  verdict{deciding}: trend={v.trend}  setup={v.strategy or 'none'}  "
                f"entry={v.entry_status}  decision={v.decision}  "
                f"conditions={v.conditions_met}/{v.conditions_evaluated}"
            )
            for name, met, detail in v.conditions:
                lines.append(f"    {name}: {met} — {detail}")
            lines.append(f"    reason: {v.reason}")
        lines.append(
            f"  liquidity: spread={_number(brief.liquidity.spread_pct, '.3f')}%  "
            f"bid_depth={_number(brief.liquidity.bid_depth_notional, '.6g')} USDT  "
            f"ask_depth={_number(brief.liquidity.ask_depth_notional, '.6g')} USDT"
        )
        if brief.limited_history:
            lines.append(
                "  history: LIMITED — too few candles for the full EMA stack on at "
                "least one timeframe; treat this setup as lower-confidence"
            )

    return "\n".join(lines)

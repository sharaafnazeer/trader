"""Named strategies from the trader's method, and the two-question verdict.

The engine used to answer one question — *is this coin bullish or bearish?* — and its
answer was read as though it were a different one: *should I take this trade now?* Those
come apart badly, and the gap is where money is lost. This module asks both, separately:

1. **What is the trend?**  — decided by the *trend* conditions.
2. **Is there a valid entry now?** — decided by the *entry* conditions.

A trend without an entry is a **WAIT**, and WAIT is expected to be the common answer. A
healthy watchlist is mostly coins in a trend that are not at an entry, and nothing here
tips a short checklist into a trade: the decision is LONG or SHORT only when *every*
evaluated entry condition is met, and WAIT otherwise.

The strategies are the trader's own, recorded verbatim in
``.sdd/strategy-catalogue/rules.md``:

* **1A — Long with the trend (pullback)**
* **1B — Short with the trend (retracement)**

They are one checklist read in two directions. The full method has seven conditions;
:func:`evaluate` judges all seven — the moving-average stack and market structure (trend),
and position relative to the EMA21-EMA50 retracement zone, the trendline, the reaction
candle, the Stochastic RSI turn and MACD confirmation (entry).
:attr:`SetupVerdict.conditions_evaluated` reports how many were judged, which stays useful
as the catalogue grows: a READY must never imply more of the method was applied than was.

**Reward-to-risk is deliberately not a condition here.** The plan's take-profit is floored
at the configured ratio, so every plan already satisfies 1:2 and a condition testing it
would pass every time. What is worth knowing is whether the target is a real swing level or
one the planner manufactured to reach the ratio, and that travels on the plan itself as
``target_is_structural`` — reported, and used to rank, never to block. Gating on it would
reject every coin at new highs, which by definition has no level above it.

This module reads *computed facts*, never candles: a full checklist can be exercised in a
test without building a price series. It belongs to the analysis core — pure and
deterministic, no I/O, and it does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from trader.config import (
    DEFAULT_STRATEGY_CROSS_LOOKBACK,
    DEFAULT_STRATEGY_MAX_EXTENSION_ATR,
    DEFAULT_STRATEGY_STOCH_OVERBOUGHT,
    DEFAULT_STRATEGY_STOCH_OVERSOLD,
    DEFAULT_STRATEGY_TRENDLINE_TOLERANCE_ATR,
)
from trader.direction import Direction
from trader.indicators import TimeframeFeatures
from trader.patterns import Pattern, is_bullish
from trader.structure import Structure, StructureState
from trader.trendline import Trendline, distance_atr

# How many conditions the full method has, against which a partial checklist is reported.
# Trend: stack, structure. Entry: zone, trendline, reaction candle, Stoch turn, MACD.
#
# The trendline is an *entry* condition rather than a trend one even though the method lists
# it under "established uptrend". What is judged here is not "does a rising line exist" but
# "has price returned to it", and a coin in a clean uptrend trading far from its trendline
# should read WAIT — a setup to watch — rather than "no setup matched", which is what a
# failed trend condition means.
METHOD_CONDITION_COUNT = 7

CONDITION_STACK = "ma_stack"
CONDITION_STRUCTURE = "structure"
CONDITION_ZONE = "zone"
CONDITION_MOMENTUM_TURN = "momentum_turn"
CONDITION_MACD = "macd"
CONDITION_REACTION = "reaction"
CONDITION_TRENDLINE = "trendline"

# The conditions judged today, in the order they are reported.
TREND_CONDITIONS: tuple[str, ...] = (CONDITION_STACK, CONDITION_STRUCTURE)
ENTRY_CONDITIONS: tuple[str, ...] = (
    CONDITION_ZONE,
    CONDITION_TRENDLINE,
    CONDITION_REACTION,
    CONDITION_MOMENTUM_TURN,
    CONDITION_MACD,
)


class StrategyId(StrEnum):
    """The catalogue's named setups. Values are the trader's own labels."""

    TREND_PULLBACK_LONG = "1A"
    TREND_PULLBACK_SHORT = "1B"
    BREAKOUT_LONG = "2A"
    BREAKOUT_SHORT = "2B"


class EntryStatus(StrEnum):
    """Whether the entry conditions are satisfied *now*."""

    READY = "READY"
    NOT_READY = "NOT_READY"


@dataclass(frozen=True)
class Condition:
    """One checklist row: whether it is met, and the measurement behind it.

    ``met`` is ``None`` when the condition could not be judged at all — an absent
    indicator, not a failure. That is a third outcome on purpose: "the 200-period average
    does not exist yet" and "the stack is in the wrong order" are different facts and a
    trader reading a WAIT deserves to know which.

    ``detail`` always names the quantity *with its value*, because a blocker the trader
    cannot check against their own chart is one they cannot learn from.
    """

    name: str
    met: bool | None
    detail: str


@dataclass(frozen=True)
class SetupVerdict:
    """The four-part answer for one coin, plus the checklist that produced it.

    ``trend`` is what the trend conditions found; ``strategy`` is the setup matched, or
    ``None`` when the trend conditions rule one out entirely — a coin with no trend is not
    "waiting" for a trend-pullback entry, it simply is not this setup. ``decision`` is the
    trend direction when every evaluated entry condition is met, and ``Direction.NONE``
    (rendered WAIT) otherwise.
    """

    symbol: str
    trend: Direction
    strategy: StrategyId | None
    entry_status: EntryStatus
    decision: Direction
    reason: str
    conditions: tuple[Condition, ...]
    conditions_evaluated: int = METHOD_CONDITION_COUNT

    @property
    def conditions_met(self) -> int:
        """How many judged conditions are satisfied — the ranking key."""
        return sum(1 for c in self.conditions if c.met is True)

    @property
    def is_ready(self) -> bool:
        """Whether this is a trade to take now."""
        return self.entry_status is EntryStatus.READY

    def condition(self, name: str) -> Condition | None:
        """The judged condition with ``name``, or ``None`` if it was not judged."""
        for condition in self.conditions:
            if condition.name == name:
                return condition
        return None


def stack_direction(features: TimeframeFeatures) -> Direction:
    """The direction implied by ``EMA10 > EMA21 > EMA50 > SMA200``, mirrored for a short.

    ``NONE`` when the averages are not cleanly stacked, and also when the 200-period
    average is absent — every comparison against ``NaN`` is false, which is the honest
    outcome for a coin with no long-term filter to judge against.
    """

    if features.ema10 > features.ema21 > features.ema50 > features.sma200:
        return Direction.LONG
    if features.ema10 < features.ema21 < features.ema50 < features.sma200:
        return Direction.SHORT
    return Direction.NONE


def zone_bounds(features: TimeframeFeatures) -> tuple[float, float] | None:
    """The EMA21-EMA50 retracement band as ``(low, high)``, or ``None`` if unreadable."""

    low, high = sorted((features.ema21, features.ema50))
    if not math.isfinite(low) or not math.isfinite(high):
        return None
    return low, high


def _stack_condition(features: TimeframeFeatures, trend: Direction) -> Condition:
    """Whether the method's stack is ordered the trade's way on this timeframe."""

    if not math.isfinite(features.sma200):
        return Condition(
            CONDITION_STACK,
            None,
            "200-period average unavailable — too little history to judge the stack",
        )

    ordering = (
        f"EMA10 {features.ema10:.6g} / EMA21 {features.ema21:.6g} / "
        f"EMA50 {features.ema50:.6g} / SMA200 {features.sma200:.6g}"
    )
    found = stack_direction(features)
    if found is trend and trend is not Direction.NONE:
        return Condition(CONDITION_STACK, True, f"stacked for a {trend.value.lower()}: {ordering}")
    return Condition(CONDITION_STACK, False, f"not stacked for the trade: {ordering}")


def _structure_condition(structure: StructureState, trend: Direction) -> Condition:
    """Higher highs and higher lows for a long; lower highs and lower lows for a short.

    The detail quotes the swing levels the classification was drawn from, not just the
    classification. "Structure is BROKEN" is a verdict the trader has to take on trust;
    "last swings 120 / 80" is something they can find on their own chart and disagree with.
    """

    wanted = Structure.BULLISH if trend is Direction.LONG else Structure.BEARISH
    shape = "higher highs and higher lows" if trend is Direction.LONG else (
        "lower highs and lower lows"
    )
    high, low = structure.last_swing_high, structure.last_swing_low
    swings = (
        f"last swings {high:.6g} / {low:.6g}"
        if high is not None and low is not None
        else "no swing pair detected"
    )
    if structure.structure is wanted:
        return Condition(CONDITION_STRUCTURE, True, f"{shape} confirmed ({swings})")
    return Condition(
        CONDITION_STRUCTURE,
        False,
        f"structure is {structure.structure.value}, not {shape} ({swings})",
    )


def _zone_condition(
    close: float, features: TimeframeFeatures, trend: Direction, max_extension_atr: float
) -> Condition:
    """Whether price has pulled back to the EMA21-EMA50 band rather than run away from it.

    The distance is read *in the trade's own direction*: a long above the band is extended,
    a long below it has merely overshot the pullback. Both are outside the band, and only
    the first is the "chasing" case the method exists to refuse — so the sign matters and
    an absolute distance would conflate them.
    """

    bounds = zone_bounds(features)
    atr = features.atr
    if bounds is None or not math.isfinite(atr) or atr <= 0.0 or not math.isfinite(close):
        return Condition(CONDITION_ZONE, None, "zone unreadable — missing averages or ATR")

    low, high = bounds
    band = f"{low:.6g}-{high:.6g}"
    if low <= close <= high:
        return Condition(CONDITION_ZONE, True, f"price {close:.6g} is inside the {band} zone")

    # Signed overshoot in the trade's direction: positive means "gone the way I wanted,
    # past the entry", which is the extended case.
    overshoot = (close - high) / atr if trend is Direction.LONG else (low - close) / atr
    if overshoot <= 0.0:
        side = "below" if trend is Direction.LONG else "above"
        return Condition(
            CONDITION_ZONE,
            True,
            f"price {close:.6g} is {abs(overshoot):.1f} ATR {side} the {band} zone, "
            "on the pullback side",
        )
    if overshoot <= max_extension_atr:
        return Condition(
            CONDITION_ZONE,
            True,
            f"price {close:.6g} is {overshoot:.1f} ATR beyond the {band} zone, "
            f"within the {max_extension_atr:.1f} ATR limit",
        )
    side = "above" if trend is Direction.LONG else "below"
    anchor = "resistance" if trend is Direction.SHORT else "support"
    return Condition(
        CONDITION_ZONE,
        False,
        f"extended {overshoot:.1f} ATR {side} the {band} zone "
        f"(limit {max_extension_atr:.1f}) — wait for a pullback to that {anchor}",
    )


def _momentum_turn_condition(
    features: TimeframeFeatures,
    trend: Direction,
    *,
    oversold: float,
    overbought: float,
    cross_lookback: int,
) -> Condition:
    """Whether momentum has *turned* the trade's way, from an extreme.

    Two halves, each rejecting a different mistake. Without the **cross**, a coin that is
    merely oversold reads as a signal — and a falling knife is oversold the whole way down.
    Without the **extreme**, a mid-range wobble qualifies, and mid-range wobbles are noise.
    The method's phrase is "reaches or approaches oversold", so the extreme is honoured as
    the level %K came *from* on its way into the cross rather than its value on the crossing
    bar exactly.
    """

    k, d = features.stoch_rsi_k, features.stoch_rsi_d
    if not math.isfinite(k) or not math.isfinite(d):
        return Condition(
            CONDITION_MOMENTUM_TURN, None, "Stochastic RSI unavailable — too little history"
        )

    reading = f"%K {k:.2f} / %D {d:.2f}"
    bars_ago = features.stoch_cross_bars_ago
    wanted_up = trend is Direction.LONG
    crossed = features.stoch_crossed_up if wanted_up else features.stoch_crossed_down
    direction_word = "above" if wanted_up else "below"

    if bars_ago is None or not crossed:
        return Condition(
            CONDITION_MOMENTUM_TURN,
            False,
            f"%K has not crossed {direction_word} %D ({reading}) — momentum has not turned",
        )
    if bars_ago > cross_lookback:
        return Condition(
            CONDITION_MOMENTUM_TURN,
            False,
            f"%K crossed {direction_word} %D {bars_ago} bars ago, beyond the "
            f"{cross_lookback}-bar window ({reading}) — the turn is stale",
        )

    extreme = features.stoch_cross_extreme
    threshold = oversold if wanted_up else overbought
    from_extreme = (
        math.isfinite(extreme)
        and (extreme <= threshold if wanted_up else extreme >= threshold)
    )
    if not from_extreme:
        limit = "oversold" if wanted_up else "overbought"
        return Condition(
            CONDITION_MOMENTUM_TURN,
            False,
            f"%K crossed {direction_word} %D {bars_ago} bars ago but from {extreme:.2f}, "
            f"not from {limit} ({threshold:.2f}) — the cross was not from an extreme",
        )
    return Condition(
        CONDITION_MOMENTUM_TURN,
        True,
        f"%K crossed {direction_word} %D {bars_ago} bars ago from {extreme:.2f} ({reading})",
    )


def _macd_condition(
    features: TimeframeFeatures, trend: Direction, *, cross_lookback: int
) -> Condition:
    """MACD confirmation: a directional crossover **and/or** the right side of zero.

    The "and/or" is the method's, and is honoured rather than tidied into one rule: either
    reading satisfies the condition on its own, and the detail says which did. They are not
    the same evidence — a crossover is momentum *turning*, the zero line is momentum's
    *sign* — so a crossover above zero is the strongest reading and is reported as such,
    letting the trader tell it from a crossover deep in negative territory without opening
    the chart.
    """

    macd, signal = features.macd, features.macd_signal
    if not math.isfinite(macd) or not math.isfinite(signal):
        return Condition(CONDITION_MACD, None, "MACD unavailable — too little history")

    wanted_up = trend is Direction.LONG
    bars_ago = features.macd_cross_bars_ago
    crossed = features.macd_crossed_up if wanted_up else features.macd_crossed_down
    fresh_cross = crossed and bars_ago is not None and bars_ago <= cross_lookback

    zero_side = "above" if macd > 0.0 else "below"
    zero_ok = macd > 0.0 if wanted_up else macd < 0.0
    reading = f"MACD {macd:.6g} / signal {signal:.6g}, {zero_side} zero"
    direction_word = "above" if wanted_up else "below"

    if fresh_cross and zero_ok:
        return Condition(
            CONDITION_MACD,
            True,
            f"crossed {direction_word} signal {bars_ago} bars ago and {zero_side} zero "
            f"— both confirm ({reading})",
        )
    if fresh_cross:
        return Condition(
            CONDITION_MACD,
            True,
            f"crossed {direction_word} signal {bars_ago} bars ago, though still "
            f"{zero_side} zero ({reading})",
        )
    if zero_ok:
        age = f"last cross {bars_ago} bars ago" if bars_ago is not None else "no recent cross"
        return Condition(
            CONDITION_MACD, True, f"{zero_side} zero ({age}) ({reading})"
        )
    return Condition(
        CONDITION_MACD,
        False,
        f"no {direction_word} crossover within {cross_lookback} bars and {zero_side} zero "
        f"({reading})",
    )


def _trendline_condition(
    line: Trendline | None,
    trend: Direction,
    close: float,
    atr: float,
    *,
    tolerance_atr: float,
) -> Condition:
    """Whether a directional trendline exists and price has returned to it.

    Two things at once, because either alone is uninformative: a rising line under a long
    that price is nowhere near is a line for later, and price sitting on a line that slopes
    the wrong way is support in a downtrend. An absent line is reported as unjudgeable
    rather than failed — too few pivots is missing evidence, not evidence against.
    """

    if line is None:
        return Condition(
            CONDITION_TRENDLINE,
            None,
            "no trendline — too few swing pivots, or they do not lie on a line",
        )

    wanted_rising = trend is Direction.LONG
    shape = "rising" if wanted_rising else "falling"
    found = "rising" if line.is_rising else ("falling" if line.is_falling else "flat")
    quality = f"{line.touches} touches, fit {line.r_squared:.2f}"
    if (line.is_rising if wanted_rising else line.is_falling) is False:
        return Condition(
            CONDITION_TRENDLINE,
            False,
            f"trendline is {found}, not {shape} ({quality})",
        )

    gap = distance_atr(line, close, atr)
    if not math.isfinite(gap):
        return Condition(
            CONDITION_TRENDLINE, None, f"{shape} trendline found but ATR is unusable"
        )

    # Read in the trade's direction: a long *above* its rising line has run away from it,
    # a long below has broken it. Only distance in the runaway direction is "not returned".
    away = gap if wanted_rising else -gap
    anchor = "support" if wanted_rising else "resistance"
    if abs(away) <= tolerance_atr:
        return Condition(
            CONDITION_TRENDLINE,
            True,
            f"price {close:.6g} is {abs(away):.1f} ATR from the {shape} trendline at "
            f"{line.level_now:.6g} ({quality})",
        )
    side = "above" if away > 0 else "below"
    return Condition(
        CONDITION_TRENDLINE,
        False,
        f"price {close:.6g} is {abs(away):.1f} ATR {side} the {shape} trendline at "
        f"{line.level_now:.6g}, beyond the {tolerance_atr:.1f} ATR tolerance — wait for a "
        f"return to that {anchor} ({quality})",
    )


def _reaction_condition(
    patterns: tuple[Pattern, ...] | None, trend: Direction, *, at_zone: bool
) -> Condition:
    """Whether the zone *rejected* price, rather than price merely reaching it.

    The condition is **positional**: a hammer somewhere in the history is irrelevant, and a
    reaction away from the zone is a reaction to something else. So it requires both a
    directionally-appropriate pattern on a recent bar *and* price being at the zone —
    which is why an unmet zone condition also makes this one unmet, rather than the two
    passing independently and jointly describing a setup that does not exist.
    """

    if patterns is None:
        return Condition(CONDITION_REACTION, None, "no candles supplied to read a reaction")

    wanted_up = trend is Direction.LONG
    matching = [p for p in patterns if is_bullish(p) is wanted_up]
    named = ", ".join(p.value for p in matching) if matching else "none"
    kind = "bullish" if wanted_up else "bearish"

    if not matching:
        seen = ", ".join(p.value for p in patterns) if patterns else "no pattern"
        return Condition(
            CONDITION_REACTION,
            False,
            f"no {kind} reaction candle on the recent bars (found: {seen})",
        )
    if not at_zone:
        return Condition(
            CONDITION_REACTION,
            False,
            f"{named} found, but not at the zone — a reaction away from the zone is a "
            "reaction to something else",
        )
    return Condition(CONDITION_REACTION, True, f"{kind} reaction at the zone: {named}")


def _reason(conditions: tuple[Condition, ...], ready: bool) -> str:
    """Compose the reason from the checklist itself, so the two cannot disagree.

    Written from the unmet rows rather than assembled separately: a reason maintained by
    hand drifts from the conditions it claims to summarise, and then the trader is reading
    a sentence the engine does not believe.
    """

    if ready:
        return "every evaluated condition is met"
    unmet = [c for c in conditions if c.met is False]
    unknown = [c for c in conditions if c.met is None]
    parts = [c.detail for c in (*unmet, *unknown)]
    return "; ".join(parts) if parts else "no conditions could be judged"


def evaluate(
    symbol: str,
    trend: Direction,
    features: TimeframeFeatures,
    structure: StructureState,
    close: float,
    *,
    patterns: tuple[Pattern, ...] | None = None,
    trendline: Trendline | None = None,
    trendline_tolerance_atr: float = DEFAULT_STRATEGY_TRENDLINE_TOLERANCE_ATR,
    max_extension_atr: float = DEFAULT_STRATEGY_MAX_EXTENSION_ATR,
    stoch_oversold: float = DEFAULT_STRATEGY_STOCH_OVERSOLD,
    stoch_overbought: float = DEFAULT_STRATEGY_STOCH_OVERBOUGHT,
    cross_lookback: int = DEFAULT_STRATEGY_CROSS_LOOKBACK,
) -> SetupVerdict:
    """Judge one coin's checklist on the decision timeframe.

    ``trend`` is the direction the engine's multi-timeframe rule decided; the trend
    conditions then verify it *on this timeframe*, so a coin whose higher timeframes agree
    but whose decision timeframe is not stacked matches no setup rather than waiting on
    one. ``features``, ``structure`` and ``close`` are all that timeframe's — the same one
    the trade plan's levels come from, so the verdict and the plan describe one trade.
    """

    stack = _stack_condition(features, trend)
    structure_row = _structure_condition(structure, trend)
    trend_rows = (stack, structure_row)

    # No trend, no setup. A coin failing the trend conditions is not "waiting" for a
    # trend-pullback entry — the setup does not apply to it at all, and reporting WAIT
    # would invite the trader to watch for an entry that can never arrive.
    if trend is Direction.NONE or not all(row.met for row in trend_rows):
        return SetupVerdict(
            symbol=symbol,
            trend=Direction.NONE,
            strategy=None,
            entry_status=EntryStatus.NOT_READY,
            decision=Direction.NONE,
            reason=_reason(trend_rows, ready=False),
            conditions=trend_rows,
            conditions_evaluated=len(TREND_CONDITIONS) + len(ENTRY_CONDITIONS),
        )

    zone = _zone_condition(close, features, trend, max_extension_atr)
    turn = _momentum_turn_condition(
        features,
        trend,
        oversold=stoch_oversold,
        overbought=stoch_overbought,
        cross_lookback=cross_lookback,
    )
    line = _trendline_condition(
        trendline, trend, close, features.atr, tolerance_atr=trendline_tolerance_atr
    )
    reaction = _reaction_condition(patterns, trend, at_zone=zone.met is True)
    macd = _macd_condition(features, trend, cross_lookback=cross_lookback)
    entry_rows = (zone, line, reaction, turn, macd)
    conditions = (*trend_rows, *entry_rows)

    # READY requires every evaluated entry condition to be *positively* met: an unjudgeable
    # condition is not a pass. Nothing here promotes a short checklist into a trade.
    ready = all(row.met is True for row in entry_rows)
    strategy = (
        StrategyId.TREND_PULLBACK_LONG
        if trend is Direction.LONG
        else StrategyId.TREND_PULLBACK_SHORT
    )
    return SetupVerdict(
        symbol=symbol,
        trend=trend,
        strategy=strategy,
        entry_status=EntryStatus.READY if ready else EntryStatus.NOT_READY,
        decision=trend if ready else Direction.NONE,
        reason=_reason(conditions, ready=ready),
        conditions=conditions,
        conditions_evaluated=len(TREND_CONDITIONS) + len(ENTRY_CONDITIONS),
    )

"""Strategy 2 — the breakout family (2A long, 2B short).

Kept apart from the trend-pullback checklist in :mod:`trader.strategy` because the entry
logic is a different shape, not a different set of thresholds. A pullback buys weakness
into an established trend and *gates* on the moving-average stack; a breakout buys strength
through a level and treats the stack as **context**. Reusing the pullback checklist would
therefore refuse every breakout that happens to occur in a mixed stack, which is most of
them.

The distinction the strategy exists to draw is **break** versus **confirmed breakout**.
``price > resistance`` is a break, and a break is not a trade: a bar that pokes through a
level and closes back underneath has told the trader the level *held*, which is the opposite
conclusion. So the level is read as three states — see :class:`BreakoutState` — and only the
middle one is tradeable by this strategy.

This module belongs to the analysis core: it is pure and deterministic, performs no I/O, and
does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from trader.config import (
    DEFAULT_BREAKOUT_BREAK_LOOKBACK,
    DEFAULT_BREAKOUT_LEVEL_TOLERANCE_ATR,
    DEFAULT_BREAKOUT_MAX_EXTENSION_ATR,
    DEFAULT_BREAKOUT_MIN_BODY_RATIO,
    DEFAULT_BREAKOUT_MIN_BREAK_ATR,
    DEFAULT_BREAKOUT_MIN_ROOM_ATR,
    DEFAULT_BREAKOUT_MIN_TOUCHES,
    DEFAULT_BREAKOUT_VOLUME_MULTIPLE,
)
from trader.direction import Direction
from trader.indicators import TimeframeFeatures
from trader.market_data import Candles
from trader.strategy import Condition, EntryStatus, SetupVerdict, StrategyId
from trader.structure import Structure, StructureState

# The confluence the trader specified, in the order it is reported. Reward-to-risk is not
# among them for the same reason it is not a pullback condition: the trade planner floors
# every target at ``target_rr``, so a checklist row testing the plan's own figure would pass
# every time. ``room`` is the honest version of that test — it asks whether the *market*
# leaves space for the trade, which the planner cannot manufacture.
CONDITION_LEVEL = "level"
CONDITION_BREAK = "break"
CONDITION_BREAK_CANDLE = "break_candle"
CONDITION_BREAK_VOLUME = "break_volume"
CONDITION_CONTINUATION = "continuation"
CONDITION_ROOM = "room"
CONDITION_NOT_EXTENDED = "not_extended"

BREAKOUT_CONDITIONS: tuple[str, ...] = (
    CONDITION_LEVEL,
    CONDITION_BREAK,
    CONDITION_BREAK_CANDLE,
    CONDITION_BREAK_VOLUME,
    CONDITION_CONTINUATION,
    CONDITION_ROOM,
    CONDITION_NOT_EXTENDED,
)
BREAKOUT_CONDITION_COUNT = len(BREAKOUT_CONDITIONS)


class BreakoutState(StrEnum):
    """The three states around a level, plus the absence of a break.

    ``CANDIDATE`` is a close beyond the level — the aggressive entry Strategy 2 takes.
    ``CONFIRMED`` is a close beyond that has since been retested and held, which is
    Strategy 3's setup and is reported here rather than traded. ``FAILED`` is the case the
    strategy exists to refuse: price traded through the level and closed back on the
    original side, which is evidence the level *held*.
    """

    NONE = "none"
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class LevelKind(StrEnum):
    """Which side of price a level sat on before it was broken."""

    RESISTANCE = "resistance"
    SUPPORT = "support"


@dataclass(frozen=True)
class Level:
    """A horizontal level drawn from repeated swing pivots at the same price.

    ``touches`` is what makes it "established" rather than a single turning point: one
    pivot is a place price happened to reverse, several at the same price is a level other
    participants are also watching. The strategy will not trade a break of a one-touch
    level for that reason.
    """

    price: float
    touches: int
    kind: LevelKind


@dataclass(frozen=True)
class _Bar:
    """One candle with the geometry the breakout tests read."""

    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low


def _bars(candles: Candles) -> list[_Bar]:
    """The frame as bars, dropping any row with a non-finite value."""

    frame = candles.frame
    rows = zip(
        frame["open"].tolist(),
        frame["high"].tolist(),
        frame["low"].tolist(),
        frame["close"].tolist(),
        frame["volume"].tolist(),
        strict=True,
    )
    return [
        _Bar(float(o), float(h), float(low), float(c), float(v))
        for o, h, low, c, v in rows
        if all(math.isfinite(float(x)) for x in (o, h, low, c, v))
    ]


def find_levels(
    structure: StructureState,
    atr: float,
    *,
    min_touches: int = DEFAULT_BREAKOUT_MIN_TOUCHES,
    tolerance_atr: float = DEFAULT_BREAKOUT_LEVEL_TOLERANCE_ATR,
) -> tuple[Level, ...]:
    """Cluster swing pivots into established horizontal levels.

    Pivots within ``tolerance_atr`` ATR of one another are one level, priced at their mean:
    real levels are a band a few ticks wide rather than a number, and demanding exact
    equality would find none. Clusters below ``min_touches`` are discarded — that threshold
    is the whole difference between "a level" and "somewhere price once turned".

    Swing highs yield resistance and swing lows yield support, which is their meaning
    *before* any break; :class:`LevelKind` therefore records where the level sat
    historically, not where it sits relative to price now.
    """

    if not math.isfinite(atr) or atr <= 0.0:
        return ()
    width = tolerance_atr * atr
    levels: list[Level] = []
    for pivots, kind in (
        (structure.swing_highs, LevelKind.RESISTANCE),
        (structure.swing_lows, LevelKind.SUPPORT),
    ):
        usable = sorted(p for p in pivots if math.isfinite(p))
        cluster: list[float] = []
        for price in usable:
            if cluster and price - cluster[0] > width:
                levels.append(_as_level(cluster, kind, min_touches))
                cluster = []
            cluster.append(price)
        if cluster:
            levels.append(_as_level(cluster, kind, min_touches))
    return tuple(lvl for lvl in levels if lvl.touches >= min_touches)


def _as_level(cluster: list[float], kind: LevelKind, min_touches: int) -> Level:
    """One pivot cluster as a level. Filtered by ``min_touches`` by the caller."""

    return Level(price=sum(cluster) / len(cluster), touches=len(cluster), kind=kind)


def _break_state(bars: list[_Bar], level: float, *, bullish: bool, lookback: int) -> tuple[
    BreakoutState, int | None
]:
    """Classify the most recent interaction with ``level``, and say which bar broke it.

    Read newest-first over ``lookback`` bars. A *close* beyond the level is the break; the
    high or low reaching through it is not, which is the distinction the whole strategy
    rests on. Once a breaking bar is found, any later bar closing back on the original side
    turns the reading into ``FAILED`` — the level held after all — and a later bar that
    returned to the level and closed beyond it again is ``CONFIRMED``, which belongs to
    Strategy 3.
    """

    if not bars:
        return BreakoutState.NONE, None

    def beyond(bar: _Bar) -> bool:
        return bar.close > level if bullish else bar.close < level

    def pierced(bar: _Bar) -> bool:
        return bar.high > level if bullish else bar.low < level

    window = bars[-lookback:] if lookback > 0 else []
    if not window:
        return BreakoutState.NONE, None

    # The break is the *oldest* bar in the window that closed beyond the level while its
    # predecessor had not: that is the bar that did the breaking, not merely a bar that
    # happens to sit beyond a level broken long ago.
    break_index: int | None = None
    start = len(bars) - len(window)
    for offset, bar in enumerate(window):
        index = start + offset
        previous = bars[index - 1] if index > 0 else None
        if beyond(bar) and (previous is None or not beyond(previous)):
            break_index = index
            break

    if break_index is None:
        # Nothing broke within the window. A bar that pierced but closed back is still the
        # informative case — the level was tested and rejected price.
        latest = bars[-1]
        if pierced(latest) and not beyond(latest):
            return BreakoutState.FAILED, None
        return BreakoutState.NONE, None

    after = bars[break_index + 1 :]
    if any(not beyond(bar) for bar in after):
        return BreakoutState.FAILED, break_index
    returned = any(
        (bar.low <= level if bullish else bar.high >= level) for bar in after
    )
    if returned and after:
        return BreakoutState.CONFIRMED, break_index
    return BreakoutState.CANDIDATE, break_index


def _level_condition(level: Level | None, min_touches: int) -> Condition:
    """Whether an established level exists to break at all."""

    if level is None:
        return Condition(
            CONDITION_LEVEL,
            False,
            f"no level with {min_touches} or more touches — nothing established to break",
        )
    return Condition(
        CONDITION_LEVEL,
        True,
        f"{level.kind.value} at {level.price:.6g} with {level.touches} touches",
    )


def _break_condition(
    state: BreakoutState, close: float, level: float, atr: float, *, min_break_atr: float
) -> Condition:
    """Whether price closed *convincingly* beyond the level, rather than reaching it.

    Two failures are reported separately because they mean opposite things. ``FAILED`` is a
    level that rejected price — evidence *against* the trade, and per the method potentially
    evidence for the opposite one. A close that merely clipped the level is not rejection;
    it is a break too small to distinguish from noise at this volatility.
    """

    distance = abs(close - level) / atr
    if state is BreakoutState.FAILED:
        return Condition(
            CONDITION_BREAK,
            False,
            f"price traded through {level:.6g} and closed back at {close:.6g} — the level "
            "held; a failed breakout is evidence for the level, not against it",
        )
    if state is BreakoutState.NONE:
        return Condition(
            CONDITION_BREAK,
            False,
            f"no close beyond {level:.6g} in the recent bars — the level is intact",
        )
    if distance < min_break_atr:
        return Condition(
            CONDITION_BREAK,
            False,
            f"closed {distance:.2f} ATR beyond {level:.6g}, short of the "
            f"{min_break_atr:.2f} ATR needed to call it a break rather than a touch",
        )
    if state is BreakoutState.CONFIRMED:
        return Condition(
            CONDITION_BREAK,
            True,
            f"closed {distance:.2f} ATR beyond {level:.6g} and has already retested it "
            "(a Strategy 3 setup, tradeable here too)",
        )
    return Condition(
        CONDITION_BREAK, True, f"closed {distance:.2f} ATR beyond {level:.6g}"
    )


def _break_candle_condition(
    bar: _Bar | None, level: float, *, bullish: bool, min_body_ratio: float
) -> Condition:
    """Whether the breaking candle is a body through the level, not a wick through it.

    The method's phrase is "isn't just a wick", and the test is positional as well as
    proportional: a candle with a healthy body that nonetheless left most of its *range*
    beyond the level as an upper wick is a candle that was pushed back, whatever its body
    ratio says.
    """

    if bar is None:
        return Condition(CONDITION_BREAK_CANDLE, None, "no breaking candle to read")
    if bar.range <= 0.0:
        return Condition(
            CONDITION_BREAK_CANDLE, None, "breaking candle has no range — cannot judge it"
        )

    body_ratio = bar.body / bar.range
    rejection_wick = bar.upper_wick if bullish else bar.lower_wick
    wick_ratio = rejection_wick / bar.range
    shape = f"body {body_ratio:.0%} of range, rejection wick {wick_ratio:.0%}"

    if body_ratio < min_body_ratio:
        return Condition(
            CONDITION_BREAK_CANDLE,
            False,
            f"the breaking candle is mostly wick ({shape}, floor {min_body_ratio:.0%}) — "
            f"price visited {level:.6g} rather than clearing it",
        )
    if wick_ratio > body_ratio:
        return Condition(
            CONDITION_BREAK_CANDLE,
            False,
            f"the breaking candle was pushed back ({shape}) — more of its range is "
            "rejection than body",
        )
    return Condition(CONDITION_BREAK_CANDLE, True, f"a real body through the level ({shape})")


def _break_volume_condition(
    bar: _Bar | None, bars: list[_Bar], *, multiple: float, window: int
) -> Condition:
    """Whether volume expanded on the break.

    Volume is *critical* for this family rather than merely helpful: a level giving way on
    ordinary volume is a level nobody defended, which is a weaker fact than the price alone
    suggests. The comparison is against the same rolling window the rest of the engine uses
    for relative volume, so the two numbers mean the same thing.
    """

    if bar is None:
        return Condition(
            CONDITION_BREAK_VOLUME, None, "no breaking candle to measure volume against"
        )
    index = bars.index(bar) if bar in bars else len(bars) - 1
    history = [b.volume for b in bars[max(0, index - window) : index] if b.volume > 0.0]
    if not history:
        return Condition(
            CONDITION_BREAK_VOLUME, None, "no volume history to compare the break against"
        )
    average = sum(history) / len(history)
    if average <= 0.0:
        return Condition(CONDITION_BREAK_VOLUME, None, "average volume is zero")
    ratio = bar.volume / average
    reading = f"{ratio:.2f}x the {len(history)}-bar average"
    if ratio < multiple:
        return Condition(
            CONDITION_BREAK_VOLUME,
            False,
            f"volume did not expand on the break — {reading}, below the {multiple:.2f}x "
            "the method asks for",
        )
    return Condition(CONDITION_BREAK_VOLUME, True, f"volume expanded on the break: {reading}")


def _continuation_condition(structure: StructureState, direction: Direction) -> Condition:
    """Whether structure is at least not arguing against continuation.

    Deliberately weaker than the pullback family's structure condition. There, structure is
    the setup; here the trader's own table rates it *context*, and a breakout out of a range
    is frequently the bar that ends a sideways structure — demanding an established trend
    would refuse exactly the setups this strategy exists to catch. So it fails only when
    structure points the *other* way.
    """

    opposing = Structure.BEARISH if direction is Direction.LONG else Structure.BULLISH
    if structure.structure is opposing:
        against = "lower highs and lower lows" if opposing is Structure.BEARISH else (
            "higher highs and higher lows"
        )
        return Condition(
            CONDITION_CONTINUATION,
            False,
            f"structure is {structure.structure.value} ({against}) — it argues against "
            "continuation in the break's direction",
        )
    return Condition(
        CONDITION_CONTINUATION,
        True,
        f"structure is {structure.structure.value} — not arguing against continuation",
    )


def _room_condition(
    close: float,
    levels: tuple[Level, ...],
    broken: Level | None,
    atr: float,
    *,
    direction: Direction,
    min_room_atr: float,
) -> Condition:
    """Whether the next level leaves the trade somewhere to go.

    This is the honest form of the reward-to-risk test. The planner floors every target at
    its configured ratio, so asking the plan whether it reaches 1:2 always answers yes; the
    market's own geometry can still say no. A breakout into a level one ATR overhead is a
    trade with nowhere to travel however the plan is drawn.
    """

    if broken is None:
        return Condition(CONDITION_ROOM, None, "no broken level — nothing to measure from")
    ahead = [
        lvl.price
        for lvl in levels
        if lvl is not broken
        and (lvl.price > close if direction is Direction.LONG else lvl.price < close)
    ]
    if not ahead:
        return Condition(
            CONDITION_ROOM,
            True,
            "no charted level ahead — open space in the break's direction",
        )
    nearest = min(ahead) if direction is Direction.LONG else max(ahead)
    room = abs(nearest - close) / atr
    if room < min_room_atr:
        return Condition(
            CONDITION_ROOM,
            False,
            f"only {room:.2f} ATR to the next level at {nearest:.6g}, short of the "
            f"{min_room_atr:.2f} ATR the trade needs to be worth taking",
        )
    return Condition(
        CONDITION_ROOM, True, f"{room:.2f} ATR of room to the next level at {nearest:.6g}"
    )


def _not_extended_condition(
    close: float, level: float, atr: float, *, max_extension_atr: float
) -> Condition:
    """Whether the break is still entry-able, or has already run away from it.

    The breakout family's version of the pullback family's chase rule, and the same mistake
    in a different shape: a good idea at a bad price. When it fails the answer is not "no
    setup" but *wait for the retest* — the level is still the level, and price coming back
    to it is Strategy 3's entry.
    """

    extension = abs(close - level) / atr
    if extension > max_extension_atr:
        return Condition(
            CONDITION_NOT_EXTENDED,
            False,
            f"price is {extension:.2f} ATR beyond {level:.6g} (limit "
            f"{max_extension_atr:.2f}) — wait for the retest rather than chasing the break",
        )
    return Condition(
        CONDITION_NOT_EXTENDED,
        True,
        f"price is {extension:.2f} ATR beyond {level:.6g}, still within reach of the entry",
    )


def _reason(conditions: tuple[Condition, ...], ready: bool) -> str:
    """Compose the reason from the unmet rows, so the two cannot disagree."""

    if ready:
        return "every evaluated condition is met"
    unmet = [c for c in conditions if c.met is False]
    unknown = [c for c in conditions if c.met is None]
    parts = [c.detail for c in (*unmet, *unknown)]
    return "; ".join(parts) if parts else "no conditions could be judged"


def _pick_break(
    bars: list[_Bar],
    levels: tuple[Level, ...],
    *,
    lookback: int,
) -> tuple[Level | None, BreakoutState, int | None, Direction]:
    """Find the level price has most recently broken, and which way.

    Direction comes from the break itself rather than from the engine's trend rule: that is
    the family's defining difference, and taking direction from the stack would reintroduce
    the gate the strategy is meant not to have. A resistance broken upward is a long, a
    support broken downward is a short.

    Where both sides show a break the *nearer* level wins, since that is the one price is
    actually reacting to. A ``FAILED`` reading is preferred over ``NONE`` so the rejection
    is reported rather than silently dropped.
    """

    if not bars:
        return None, BreakoutState.NONE, None, Direction.NONE
    close = bars[-1].close
    best: tuple[Level | None, BreakoutState, int | None, Direction] = (
        None,
        BreakoutState.NONE,
        None,
        Direction.NONE,
    )
    best_distance = math.inf
    for level in levels:
        # Direction comes from what the level *is*, not from where price sits now.
        # Resistance can only be broken upward and support only downward; reading the side
        # price happens to be on would call a coin trading under its resistance a "bearish
        # break" of it, which is not a break at all — it is the level doing its job.
        bullish = level.kind is LevelKind.RESISTANCE
        state, index = _break_state(bars, level.price, bullish=bullish, lookback=lookback)
        if state is BreakoutState.NONE:
            continue
        distance = abs(close - level.price)
        # A real break outranks a failure at any distance; between like readings, nearer wins.
        better = (
            best[1] is BreakoutState.NONE
            or (state is not BreakoutState.FAILED and best[1] is BreakoutState.FAILED)
            or (
                (state is BreakoutState.FAILED) == (best[1] is BreakoutState.FAILED)
                and distance < best_distance
            )
        )
        if better:
            best = (
                level,
                state,
                index,
                Direction.LONG if bullish else Direction.SHORT,
            )
            best_distance = distance
    return best


def evaluate_breakout(
    symbol: str,
    candles: Candles,
    features: TimeframeFeatures,
    structure: StructureState,
    *,
    min_touches: int = DEFAULT_BREAKOUT_MIN_TOUCHES,
    level_tolerance_atr: float = DEFAULT_BREAKOUT_LEVEL_TOLERANCE_ATR,
    min_break_atr: float = DEFAULT_BREAKOUT_MIN_BREAK_ATR,
    min_body_ratio: float = DEFAULT_BREAKOUT_MIN_BODY_RATIO,
    volume_multiple: float = DEFAULT_BREAKOUT_VOLUME_MULTIPLE,
    min_room_atr: float = DEFAULT_BREAKOUT_MIN_ROOM_ATR,
    max_extension_atr: float = DEFAULT_BREAKOUT_MAX_EXTENSION_ATR,
    break_lookback: int = DEFAULT_BREAKOUT_BREAK_LOOKBACK,
    volume_window: int = 20,
) -> SetupVerdict:
    """Judge one coin's breakout setup on the decision timeframe.

    Returns the same :class:`~trader.strategy.SetupVerdict` the pullback checklist returns,
    so every consumer — the table, the analyst brief, the backtester — reads one shape
    regardless of which strategy matched.

    Direction is discovered, not supplied. Where no established level has been broken the
    verdict is "no setup" (``strategy=None``) rather than WAIT, on the same reasoning the
    pullback family uses: a coin with nothing to break is not waiting for a breakout entry,
    the setup simply does not apply to it.
    """

    bars = _bars(candles)
    atr = features.atr
    levels = find_levels(
        structure, atr, min_touches=min_touches, tolerance_atr=level_tolerance_atr
    )

    if not bars or not math.isfinite(atr) or atr <= 0.0:
        row = Condition(CONDITION_LEVEL, None, "no candles or no ATR — cannot read a level")
        return _no_setup(symbol, (row,))

    broken, state, break_index, direction = _pick_break(
        bars, levels, lookback=break_lookback
    )
    if broken is None or direction is Direction.NONE:
        return _no_setup(symbol, (_level_condition(None, min_touches),))

    close = bars[-1].close
    break_bar = bars[break_index] if break_index is not None else None

    conditions = (
        _level_condition(broken, min_touches),
        _break_condition(state, close, broken.price, atr, min_break_atr=min_break_atr),
        _break_candle_condition(
            break_bar,
            broken.price,
            bullish=direction is Direction.LONG,
            min_body_ratio=min_body_ratio,
        ),
        _break_volume_condition(
            break_bar, bars, multiple=volume_multiple, window=volume_window
        ),
        _continuation_condition(structure, direction),
        _room_condition(
            close,
            levels,
            broken,
            atr,
            direction=direction,
            min_room_atr=min_room_atr,
        ),
        _not_extended_condition(
            close, broken.price, atr, max_extension_atr=max_extension_atr
        ),
    )

    ready = all(row.met is True for row in conditions)
    strategy = (
        StrategyId.BREAKOUT_LONG if direction is Direction.LONG else StrategyId.BREAKOUT_SHORT
    )
    return SetupVerdict(
        symbol=symbol,
        trend=direction,
        strategy=strategy,
        entry_status=EntryStatus.READY if ready else EntryStatus.NOT_READY,
        decision=direction if ready else Direction.NONE,
        reason=_reason(conditions, ready=ready),
        conditions=conditions,
        conditions_evaluated=BREAKOUT_CONDITION_COUNT,
    )


def _no_setup(symbol: str, conditions: tuple[Condition, ...]) -> SetupVerdict:
    """The verdict for a coin this strategy does not apply to."""

    return SetupVerdict(
        symbol=symbol,
        trend=Direction.NONE,
        strategy=None,
        entry_status=EntryStatus.NOT_READY,
        decision=Direction.NONE,
        reason=_reason(conditions, ready=False),
        conditions=conditions,
        conditions_evaluated=BREAKOUT_CONDITION_COUNT,
    )

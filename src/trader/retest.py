"""Breakout detection as a *sequence* rather than a price comparison.

The engine's breakout category asks one question of the latest candle: is this close
beyond the last swing pivot? That cannot tell apart the three things a trader means by
"it broke out" — a level poked through and left behind, a level broken and successfully
retested, and a level broken and then lost again. The third is not a weaker breakout; it
is evidence *against* the trade, and scoring it as partial credit is the defect this
module exists to fix.

:func:`detect` walks a window of candles looking for the ordered sequence — break,
return, hold-or-fail — and reports one of four :class:`RetestState` values. A level is
treated as a *band* whose half-width is a multiple of ATR, because a level is never a
price: without a tolerance a one-tick overshoot would count as a break and a one-tick
undershoot as a failure. Closes are decisive and wicks are not, on both sides: a close
beyond the band breaks (or loses) the level, while a wick merely reaching back into the
band is what a *retest* is.

The scoring layer receives features, structure and latest closes — it has no candle
frames — so :func:`detect_by_timeframe` is the shared helper both callers (the live
scanner and the backtest evaluation) use to turn their frames into the per-timeframe
states they pass into scoring. Both must use it, or a measured delta describes something
the scan does not do.

This module belongs to the analysis core: it is pure and deterministic, performs no I/O,
and does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import math
from enum import StrEnum

from trader.direction import Direction
from trader.indicators import TimeframeFeatures
from trader.market_data import Candles
from trader.structure import StructureState

# Defaults mirror the ``entry:`` config block; they are restated here so the detector is
# callable — and testable — without constructing a whole configuration.
DEFAULT_LOOKBACK = 30
DEFAULT_TOLERANCE_ATR = 0.25


class RetestState(StrEnum):
    """What the window says about the trade's level.

    ``NONE`` and ``BROKEN_NO_RETEST`` are both "nothing to say about a retest", but they
    are distinct: the first means the level was never taken out, the second that it was
    and price never came back. Only ``RETEST_FAILED`` is negative evidence.
    """

    NONE = "none"
    BROKEN_NO_RETEST = "broken"
    RETEST_HELD = "held"
    RETEST_FAILED = "failed"


def detect(
    candles: Candles,
    level: float,
    direction: Direction,
    atr: float,
    *,
    lookback: int = DEFAULT_LOOKBACK,
    tolerance_atr: float = DEFAULT_TOLERANCE_ATR,
) -> RetestState:
    """Classify the last ``lookback`` candles against ``level`` for ``direction``.

    For a long, ``level`` is resistance: a *break* is a close above ``level + tolerance``
    from a bar that was not already above it, a *return* is any later bar whose low
    re-enters the band (``low <= level + tolerance``), and the level is *lost* by a close
    below ``level - tolerance`` at or after that return. A short mirrors all three around
    a support level.

    A window with fewer bars than ``lookback`` cannot be judged and yields
    :attr:`RetestState.NONE` rather than raising, as does a directionless trade or an
    unusable level or ATR — an absent reading, never a fabricated one.
    """

    if direction is Direction.NONE:
        return RetestState.NONE
    if not math.isfinite(level) or not math.isfinite(atr) or atr <= 0.0:
        return RetestState.NONE

    frame = candles.frame
    if len(frame) < lookback:
        return RetestState.NONE

    window = frame.iloc[-lookback:]
    highs = [float(v) for v in window["high"].tolist()]
    lows = [float(v) for v in window["low"].tolist()]
    closes = [float(v) for v in window["close"].tolist()]

    tolerance = atr * tolerance_atr
    upper = level + tolerance
    lower = level - tolerance
    long_side = direction is Direction.LONG

    # The break: the first *close* to move decisively beyond the band from inside or below
    # it. A wick through the level is a test of it, not a break of it — and a window that
    # opens already beyond the level never witnessed the break, so it reports none rather
    # than treating its own first bar as the event.
    def beyond(close: float) -> bool:
        return (close > upper) if long_side else (close < lower)

    break_index: int | None = None
    for i in range(1, len(closes)):
        if beyond(closes[i]) and not beyond(closes[i - 1]):
            break_index = i
            break
    if break_index is None:
        return RetestState.NONE

    # The return: the first later bar that trades back into the band. Here the wick is
    # exactly what counts — price reaching the level is the retest, whatever it closes at.
    retest_index: int | None = None
    for i in range(break_index + 1, len(closes)):
        touched = (lows[i] <= upper) if long_side else (highs[i] >= lower)
        if touched:
            retest_index = i
            break
    if retest_index is None:
        return RetestState.BROKEN_NO_RETEST

    # The verdict: from the returning bar onward, one decisive close beyond the far side of
    # the band means the level was given back.
    for close in closes[retest_index:]:
        if (close < lower) if long_side else (close > upper):
            return RetestState.RETEST_FAILED
    return RetestState.RETEST_HELD


def level_for(structure: StructureState, direction: Direction) -> float | None:
    """The level a breakout would be measured against: resistance long, support short.

    ``None`` when the timeframe has no detected swing on the relevant side, which is the
    same input the existing breakout credit degrades to neutral on.
    """

    if direction is Direction.LONG:
        return structure.last_swing_high
    if direction is Direction.SHORT:
        return structure.last_swing_low
    return None


def detect_by_timeframe(
    candles_by_tf: dict[str, Candles],
    structure_by_tf: dict[str, StructureState],
    features_by_tf: dict[str, TimeframeFeatures],
    direction: Direction,
    *,
    lookback: int = DEFAULT_LOOKBACK,
    tolerance_atr: float = DEFAULT_TOLERANCE_ATR,
) -> dict[str, RetestState]:
    """Run :func:`detect` per timeframe against that timeframe's own swing level and ATR.

    Shared by the live scanner and the backtest so both compute the state identically. A
    timeframe missing structure, features or a swing level on the traded side is simply
    absent from the result, leaving the breakout credit for it exactly as it was.
    """

    states: dict[str, RetestState] = {}
    for timeframe, candles in candles_by_tf.items():
        structure = structure_by_tf.get(timeframe)
        features = features_by_tf.get(timeframe)
        if structure is None or features is None:
            continue
        level = level_for(structure, direction)
        if level is None:
            continue
        states[timeframe] = detect(
            candles,
            level,
            direction,
            features.atr,
            lookback=lookback,
            tolerance_atr=tolerance_atr,
        )
    return states

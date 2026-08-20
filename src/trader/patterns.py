"""Candlestick reaction patterns — the price-action half of the method's entry.

The method does not take a pullback into the retracement zone on its own. It waits for the
zone to *reject* price: a bullish engulfing, a hammer or pin-bar rejection, or a
morning-star reversal for a long, and their mirrors for a short. That reaction is what
separates "price is at support" from "buyers turned up at support".

Every definition here is pinned in code and asserted on hand-built bars, because textbook
definitions vary and the trader's coaches may use tighter ones. Each pattern is an
independent predicate over a handful of bars, and the thresholds that admit judgement are
parameters — so correcting a definition costs one constant and one test rather than a
redesign.

:func:`detect` reports **every** pattern present, not the first match. A bar that is both a
hammer and the close of a morning star is both, and silently reducing it to one would throw
away the stronger reading.

This module belongs to the analysis core: it is pure and deterministic, performs no I/O,
and does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from trader.market_data import Candles

# How many times the body a wick must be before it counts as a rejection. Two is the common
# textbook figure; it is a parameter because "common" is not the same as "the trader's".
DEFAULT_WICK_BODY_RATIO = 2.0

# The middle bar of a star pattern is the pause between the two moves. It must be small
# relative to the bar that preceded it, or the sequence is just two ordinary bars.
STAR_MIDDLE_BODY_FRACTION = 0.5


class Pattern(StrEnum):
    """The reaction candles the method recognises."""

    BULLISH_ENGULFING = "bullish_engulfing"
    BEARISH_ENGULFING = "bearish_engulfing"
    HAMMER = "hammer"
    SHOOTING_STAR = "shooting_star"
    MORNING_STAR = "morning_star"
    EVENING_STAR = "evening_star"


_BULLISH: frozenset[Pattern] = frozenset(
    {Pattern.BULLISH_ENGULFING, Pattern.HAMMER, Pattern.MORNING_STAR}
)


def is_bullish(pattern: Pattern) -> bool:
    """Whether ``pattern`` is a reaction *upward* — the reading a long is waiting for."""
    return pattern in _BULLISH


@dataclass(frozen=True)
class _Bar:
    """One candle reduced to the measurements the patterns are defined on."""

    open: float
    high: float
    low: float
    close: float

    @property
    def body(self) -> float:
        """Absolute distance between open and close."""
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_up(self) -> bool:
        return self.close > self.open

    @property
    def is_down(self) -> bool:
        return self.close < self.open

    @property
    def midpoint(self) -> float:
        """The middle of the body — what a star's final bar must close beyond."""
        return (self.open + self.close) / 2.0


def _bars(candles: Candles) -> list[_Bar]:
    """The frame as bars, dropping any row with a non-finite price."""

    frame = candles.frame
    rows = zip(
        frame["open"].tolist(),
        frame["high"].tolist(),
        frame["low"].tolist(),
        frame["close"].tolist(),
        strict=True,
    )
    return [
        _Bar(float(o), float(h), float(low), float(c))
        for o, h, low, c in rows
        if all(math.isfinite(float(v)) for v in (o, h, low, c))
    ]


def _is_engulfing(previous: _Bar, current: _Bar, *, bullish: bool) -> bool:
    """A reversal bar whose body wholly covers the previous, opposite-coloured one.

    The body must be *strictly larger*, so a bar merely repeating the previous one's open
    and close is not an engulfing — it is a pause, and calling it a reversal would be the
    definition doing the trader's wishing for them.
    """

    if previous.body <= 0.0 or current.body <= previous.body:
        return False
    if bullish:
        if not (previous.is_down and current.is_up):
            return False
        return current.open <= previous.close and current.close >= previous.open
    if not (previous.is_up and current.is_down):
        return False
    return current.open >= previous.close and current.close <= previous.open


def _is_pin_bar(bar: _Bar, *, ratio: float, bullish: bool) -> bool:
    """A rejection candle: one long wick, a small body, and no wick worth noting opposite.

    Bullish is the hammer (rejection from below), bearish the shooting star. A doji with no
    body at all is excluded: with a zero body every wick is infinitely long and the ratio
    stops meaning anything.
    """

    if bar.body <= 0.0:
        return False
    long_wick = bar.lower_wick if bullish else bar.upper_wick
    short_wick = bar.upper_wick if bullish else bar.lower_wick
    return long_wick >= ratio * bar.body and short_wick <= bar.body


def _is_star(first: _Bar, middle: _Bar, last: _Bar, *, bullish: bool) -> bool:
    """A three-bar reversal: a move, a pause, and a move back through its middle.

    The pause is what makes it a star — a middle bar with a body a fraction of the first's.
    The final bar must close beyond the *midpoint of the first bar's body*, which is what
    distinguishes a reversal from a weak bounce that leaves the first bar intact.
    """

    if first.body <= 0.0 or last.body <= 0.0:
        return False
    if middle.body > first.body * STAR_MIDDLE_BODY_FRACTION:
        return False
    if bullish:
        return first.is_down and last.is_up and last.close > first.midpoint
    return first.is_up and last.is_down and last.close < first.midpoint


def _patterns_ending_at(bars: list[_Bar], index: int, ratio: float) -> set[Pattern]:
    """Every pattern whose final bar is ``bars[index]``."""

    found: set[Pattern] = set()
    current = bars[index]

    if _is_pin_bar(current, ratio=ratio, bullish=True):
        found.add(Pattern.HAMMER)
    if _is_pin_bar(current, ratio=ratio, bullish=False):
        found.add(Pattern.SHOOTING_STAR)

    if index >= 1:
        previous = bars[index - 1]
        if _is_engulfing(previous, current, bullish=True):
            found.add(Pattern.BULLISH_ENGULFING)
        if _is_engulfing(previous, current, bullish=False):
            found.add(Pattern.BEARISH_ENGULFING)

    if index >= 2:
        first, middle = bars[index - 2], bars[index - 1]
        if _is_star(first, middle, current, bullish=True):
            found.add(Pattern.MORNING_STAR)
        if _is_star(first, middle, current, bullish=False):
            found.add(Pattern.EVENING_STAR)

    return found


def detect(
    candles: Candles,
    *,
    bars: int = 1,
    wick_body_ratio: float = DEFAULT_WICK_BODY_RATIO,
) -> tuple[Pattern, ...]:
    """Every pattern completing within the last ``bars`` candles, in enum order.

    ``bars`` of 1 looks only at the latest candle. A frame shorter than a pattern needs
    simply yields fewer patterns — never an error — because a young coin having too little
    history is an ordinary condition, not a failure.
    """

    frame = _bars(candles)
    if not frame:
        return ()

    found: set[Pattern] = set()
    for index in range(len(frame) - 1, max(-1, len(frame) - 1 - bars), -1):
        found |= _patterns_ending_at(frame, index, wick_body_ratio)
    # Enum order rather than set order, so the result is deterministic.
    return tuple(p for p in Pattern if p in found)

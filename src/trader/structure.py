"""Market-structure detection from swing highs and lows.

:func:`analyze` inspects a :class:`~trader.market_data.Candles` frame, detects the
swing highs and swing lows (local extrema of the ``high`` / ``low`` series), and
classifies the structure as :attr:`Structure.BULLISH` (higher highs *and* higher
lows), :attr:`Structure.BEARISH` (lower highs *and* lower lows), or
:attr:`Structure.BROKEN` (anything mixed, or too little history). The detected swing
levels are also returned so a later task's trade planner can use them as our own
support/resistance pivots rather than depending on TradingView.

This module belongs to the analysis core: it is pure and deterministic, performs no
I/O, and does not import ``typer`` or ``rich``. It is bespoke pandas-free logic over
the plain price lists, so it is trivially testable against hand-built frames.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trader.market_data import Candles

# How many candles on each side a point must dominate to count as a swing. A fractal
# pivot at index ``i`` must be strictly beyond every point in ``[i-window, i+window]``.
DEFAULT_SWING_WINDOW = 2


class Structure(StrEnum):
    """The market-structure classification of a timeframe."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    BROKEN = "BROKEN"


@dataclass(frozen=True)
class StructureState:
    """A timeframe's structure classification plus the swing levels behind it.

    ``swing_highs`` / ``swing_lows`` are the detected pivot prices in chronological
    order; they double as the support/resistance levels consumed downstream by the
    trade planner.
    """

    structure: Structure
    swing_highs: tuple[float, ...]
    swing_lows: tuple[float, ...]

    @property
    def last_swing_high(self) -> float | None:
        """The most recent swing-high price, or ``None`` if none were detected."""
        return self.swing_highs[-1] if self.swing_highs else None

    @property
    def last_swing_low(self) -> float | None:
        """The most recent swing-low price, or ``None`` if none were detected."""
        return self.swing_lows[-1] if self.swing_lows else None


def _swing_indices(values: list[float], window: int, *, want_high: bool) -> list[int]:
    """Indices of strict local extrema of ``values`` dominating ``window`` neighbors.

    With ``want_high`` a point must be strictly greater than every neighbor within
    the window (a swing high); otherwise strictly less (a swing low). Points too
    close to either end to have a full window on both sides are never pivots.
    """

    result: list[int] = []
    n = len(values)
    for i in range(window, n - window):
        pivot = values[i]
        neighbors = values[i - window : i] + values[i + 1 : i + window + 1]
        if want_high:
            if all(pivot > other for other in neighbors):
                result.append(i)
        else:
            if all(pivot < other for other in neighbors):
                result.append(i)
    return result


def _classify(swing_highs: tuple[float, ...], swing_lows: tuple[float, ...]) -> Structure:
    """Classify structure from the two most recent swing highs and lows."""

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        # Not enough confirmed swings to establish a trend either way.
        return Structure.BROKEN

    higher_highs = swing_highs[-1] > swing_highs[-2]
    higher_lows = swing_lows[-1] > swing_lows[-2]
    lower_highs = swing_highs[-1] < swing_highs[-2]
    lower_lows = swing_lows[-1] < swing_lows[-2]

    if higher_highs and higher_lows:
        return Structure.BULLISH
    if lower_highs and lower_lows:
        return Structure.BEARISH
    return Structure.BROKEN


def analyze(candles: Candles, *, swing_window: int = DEFAULT_SWING_WINDOW) -> StructureState:
    """Detect swing highs/lows and classify the structure of ``candles``.

    ``swing_window`` controls how many candles on each side a pivot must dominate; a
    larger window yields fewer, more significant swings.
    """

    frame = candles.frame
    highs = [float(value) for value in frame["high"].tolist()]
    lows = [float(value) for value in frame["low"].tolist()]

    high_idx = _swing_indices(highs, swing_window, want_high=True)
    low_idx = _swing_indices(lows, swing_window, want_high=False)

    swing_highs = tuple(highs[i] for i in high_idx)
    swing_lows = tuple(lows[i] for i in low_idx)

    return StructureState(
        structure=_classify(swing_highs, swing_lows),
        swing_highs=swing_highs,
        swing_lows=swing_lows,
    )

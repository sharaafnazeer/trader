"""Trendlines fitted through swing pivots.

The method's trend conditions are not satisfied by moving averages alone: it wants a
*rising trendline* under a long and a *falling* one over a short, and then price returning
to that line. A line is the one piece of the method that cannot be read off a single bar —
it is a claim about several pivots at once — which is why it lives in its own module and is
fitted rather than inferred.

Two properties make the fit honest rather than decorative:

* **It reports how well the pivots actually lie on it.** Any two points define a line and
  any three define one badly; a line through pivots that are not collinear is a line the
  trader would never draw. The fit's :attr:`Trendline.r_squared` is what lets a caller
  reject it by threshold instead of trusting it by default.
* **It returns nothing rather than something weak.** Too few pivots, or a poor fit, yields
  ``None`` — an absent reading, never a fabricated one. That is the same stance the rest of
  the engine takes on a missing indicator.

This module belongs to the analysis core: it is pure and deterministic, performs no I/O,
and does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

# A line needs three touches before it is a trendline rather than a pair of points: two
# points always fit perfectly and prove nothing.
DEFAULT_MIN_TOUCHES = 3

# How well the pivots must lie on the line. Below this the fit is reported as no line.
DEFAULT_MIN_R_SQUARED = 0.7

# How many of the most recent pivots to fit through. A line is a *recent* claim; dragging in
# swings from hundreds of bars ago fits the history rather than the trend in force now.
DEFAULT_MAX_PIVOTS = 5


@dataclass(frozen=True)
class Trendline:
    """A line fitted through swing pivots.

    ``slope`` is price per bar, so its sign is what makes the line rising or falling.
    ``level_now`` is the line extended to the latest bar — the price the trendline sits at
    today, which is what "price returned to the trendline" is measured against.
    ``touches`` is how many pivots it was fitted through and ``r_squared`` how well they lie
    on it.
    """

    slope: float
    level_now: float
    touches: int
    r_squared: float

    @property
    def is_rising(self) -> bool:
        return self.slope > 0.0

    @property
    def is_falling(self) -> bool:
        return self.slope < 0.0


def fit(
    pivots: Sequence[tuple[int, float]],
    latest_index: int,
    *,
    min_touches: int = DEFAULT_MIN_TOUCHES,
    min_r_squared: float = DEFAULT_MIN_R_SQUARED,
    max_pivots: int = DEFAULT_MAX_PIVOTS,
) -> Trendline | None:
    """Least-squares line through the most recent ``max_pivots`` of ``pivots``.

    ``pivots`` are ``(bar index, price)`` in chronological order and ``latest_index`` is the
    bar the line is extended to. Returns ``None`` when there are fewer than ``min_touches``
    usable pivots, when they share a single bar index (no line to draw), or when the fit
    falls below ``min_r_squared``.
    """

    usable = [
        (float(i), float(p))
        for i, p in pivots[-max_pivots:]
        if math.isfinite(i) and math.isfinite(p)
    ]
    if len(usable) < min_touches:
        return None

    n = float(len(usable))
    xs = [x for x, _ in usable]
    ys = [y for _, y in usable]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    variance_x = sum((x - mean_x) ** 2 for x in xs)
    if variance_x <= 0.0:
        # Every pivot on the same bar: there is no line through them.
        return None

    slope = sum((x - mean_x) * (y - mean_y) for x, y in usable) / variance_x
    intercept = mean_y - slope * mean_x

    residual = sum((y - (slope * x + intercept)) ** 2 for x, y in usable)
    total = sum((y - mean_y) ** 2 for y in ys)
    # A perfectly flat set of pivots has no variance to explain; the line goes exactly
    # through them, so the fit is perfect rather than undefined.
    r_squared = 1.0 if total <= 0.0 else 1.0 - residual / total

    if r_squared < min_r_squared:
        return None

    return Trendline(
        slope=slope,
        level_now=slope * float(latest_index) + intercept,
        touches=len(usable),
        r_squared=r_squared,
    )


def distance_atr(line: Trendline, close: float, atr: float) -> float:
    """How far ``close`` sits from the line, in ATR multiples and signed.

    Positive means above the line. ATR units are the only form comparable across a
    sub-cent memecoin and BTC; a percentage would call the same distance "large" on one and
    "noise" on the other. Non-finite inputs yield ``NaN`` rather than a fabricated zero.
    """

    if not math.isfinite(close) or not math.isfinite(atr) or atr <= 0.0:
        return math.nan
    if not math.isfinite(line.level_now):
        return math.nan
    return (close - line.level_now) / atr

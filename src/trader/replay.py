"""Point-in-time slicing of multi-timeframe history — the no-look-ahead guardrail.

:class:`Replay` wraps a coin's full per-timeframe history and answers a single
question honestly: *what did the market look like at moment ``ts``?* Given a
timestamp it returns, for every timeframe, a :class:`~trader.market_data.Candles`
frame containing only the bars that had **closed at or before ``ts``**. The analysis
core downstream only ever sees these truncated frames, so a future bar cannot leak
into an earlier evaluation — this slice is the *only* guard against look-ahead in the
backtest.

It also yields the ordered sequence of reference-timeframe close timestamps that drive
evaluation: the backtester marches through these, slicing at each one.

This module belongs to the analysis core: it is pure and deterministic, performs no
I/O, and does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from trader.market_data import Candles

# Milliseconds per unit of a ccxt-style timeframe suffix. Lower-case ``m`` is minutes;
# upper-case ``M`` is months (approximated at 30 days, sufficient for ordering slices).
_UNIT_MS: dict[str, int] = {
    "m": 60_000,
    "h": 3_600_000,
    "d": 86_400_000,
    "w": 604_800_000,
    "M": 2_592_000_000,
}

_TIMEFRAME_RE = re.compile(r"^(\d+)([mhdwM])$")


def timeframe_to_ms(timeframe: str) -> int:
    """Duration of one ``timeframe`` candle in milliseconds (e.g. ``"4h"`` -> 14400000).

    Supports the ccxt-style suffixes ``m`` (minute), ``h`` (hour), ``d`` (day),
    ``w`` (week), and ``M`` (month, approximated at 30 days). An unrecognized string
    raises :class:`ValueError` so a misconfigured timeframe fails loudly.
    """

    match = _TIMEFRAME_RE.match(timeframe)
    if match is None:
        raise ValueError(f"unrecognized timeframe: {timeframe!r}")
    amount = int(match.group(1))
    return amount * _UNIT_MS[match.group(2)]


@dataclass(frozen=True, eq=False)
class Replay:
    """Point-in-time slicer over a coin's full per-timeframe history.

    ``frames`` maps each timeframe to its full-history :class:`Candles`; each frame's
    ``timestamp`` column is the candle *open* time in milliseconds (the ccxt
    convention), so a candle closes at ``timestamp + timeframe_to_ms(tf)``.
    ``reference_timeframe`` is the timeframe whose closes drive evaluation. ``eq=False``
    because the wrapped frames are not scalar-comparable.

    ``max_bars`` optionally bounds each sliced frame to its last ``max_bars`` bars. The
    default of ``None`` keeps the full (unbounded) window, so existing callers are
    unchanged. When set, :meth:`slice_at` returns a fixed-size trailing window per
    timeframe — the same shape the live scanner sees — which keeps a long backtest O(N)
    instead of O(N^2). The bound is applied *after* the no-look-ahead filter, so a bar
    closing after ``ts`` is never eligible to be kept.
    """

    frames: dict[str, Candles]
    reference_timeframe: str
    max_bars: int | None = None

    def reference_closes(self) -> tuple[int, ...]:
        """The ordered reference-timeframe close timestamps that drive evaluation.

        Each is a bar's open timestamp plus one timeframe duration, i.e. the moment the
        bar closed. Ascending, one per reference-timeframe bar.
        """

        candles = self.frames[self.reference_timeframe]
        duration = timeframe_to_ms(self.reference_timeframe)
        return tuple(int(ts) + duration for ts in candles.frame["timestamp"].tolist())

    def slice_at(self, ts: int) -> dict[str, Candles]:
        """Per-timeframe frames containing only bars closed at or before ``ts``.

        For every timeframe a bar is kept when ``timestamp + duration <= ts`` — its
        close time is at or before the moment ``ts``. Bars closing after ``ts`` (the
        future) are dropped, so a spike placed after ``ts`` cannot affect the result.

        When ``max_bars`` is set, only the last ``max_bars`` of those closed bars are
        kept per timeframe. The tail is taken *after* the close-time filter, so it never
        reaches into the future — it merely trims the oldest bars the indicators no
        longer need.
        """

        sliced: dict[str, Candles] = {}
        for timeframe, candles in self.frames.items():
            duration = timeframe_to_ms(timeframe)
            frame = candles.frame
            mask = (frame["timestamp"] + duration) <= ts
            kept = frame[mask]
            if self.max_bars is not None:
                kept = kept.tail(self.max_bars)
            kept = kept.reset_index(drop=True)
            sliced[timeframe] = Candles(
                symbol=candles.symbol, timeframe=timeframe, frame=kept
            )
        return sliced
